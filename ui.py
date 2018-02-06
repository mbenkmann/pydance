'''
Copyright (C) 2018 Matthias S. Benkmann

Permission is hereby granted, free of charge, to any person obtaining a copy
of this file (originally named ui.py) and associated documentation files
(the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is furnished
to do so, subject to the following conditions:
The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
'''

import pygame
from copy import copy, deepcopy
from collections import deque
import os
from constants import *

import i18n

# for debugging purposes, we keep a history of this many most recent pygame.event.Events.
MAX_KEEP_PYGAME_EVENTS = 128

# ID numbers for semantic events returned by the UI class.
# An input event as used in pydance is a pair (pid, evid) where
# evid is one of the following event IDs (negative if the event is turned off)
# and pid identifies the dance pad
# the event is associated with: -1 (not dance-related, menuing), 0 (player 1), 1 (player 2).
# The special ID "PASS" means "event queue is empty".
# The special ID "QUIT" is a request to terminate the program asap.
(PASS, CANCEL, UP, UPLEFT, LEFT, DOWNLEFT, DOWN, DOWNRIGHT,
 RIGHT, UPRIGHT, CENTER, OPTIONS, RANDOM, SCREENSHOT,
 CONFIRM, PGUP, PGDN, FULLSCREEN, SORT, QUIT) = range(20)
EVNAMES = ("PASS", "CANCEL", "UP", "UPLEFT", "LEFT", "DOWNLEFT", "DOWN", "DOWNRIGHT",
"RIGHT", "UPRIGHT", "CENTER", "OPTIONS", "RANDOM", "SCREENSHOT",
"CONFIRM", "PGUP", "PGDN", "FULLSCREEN", "SORT", "QUIT")

# Maximum number of players. Note that old code assumes 2 players in some places.
MAX_PLAYERS = 4

# Bitmask for pid. Used on special events used with generic buttons. These have
# the controller id encoded in pid.
PLAYERS_MASK = MAX_PLAYERS - 1

# maximum buttons supported for 1 controller
MAX_BUTTONS = 32

# a number greather than any of the semantic events above. This serves as the base
# for generic button events which are used internally for learning directional
# buttons from observing how they are activated simultaneously with axes.
GENERIC_BUTTON = 64

# Bit shift for encoding controller number in generic button event's pid.
GENERIC_BUTTON_SHIFTER = 8

# Minimum number of times a generic button must have been pressed
# (with no other buttons pressed at the same time) before it can be learned as
# UP/DOWN/LEFT/RIGHT.
MIN_LEARN_GENERIC_BUTTON_COUNT = 5
# Minimum percentage of times a generic button must have been seen together with
# direction X to learn it as that direction.
LEARN_GENERIC_BUTTON_DIRECTION = .85
# Maximum percentage of times a generic button must have been seen together with
# any direction for it to be learned as independent of directions.
LEARN_GENERIC_BUTTON_INDEPENDENT = .5

# Unfortunately the old SDL version that pygame uses does not give us events when
# controllers are plugged in or removed. So in order to support hotplugging we have
# to reinit the events system regularly. Because that risks losing events, we
# only do that if we have not received any events for a certain amount of time.
# Because losing events during dancing must be avoided at all costs and because it
# is less likely that someone would un/plug controllers during a dance, the wait
# time during dancing is very high. The reason we do it during dancing at all is
# that if someone pulls out the controller during the dance on a keyboardless system
# he would have to wait for the complete song to finish before he could interact
# with the machine again. That could be several minutes.
# When the reinit has been triggered by the expiration of the *_NO_EVENT_TIME, we
# poll for new devices every *_INTERVAL until we see the first event.
POLL_REINIT_CONTROLLERS_AFTER_NO_EVENT_TIME = 3000
POLL_REINIT_CONTROLLERS_INTERVAL = 2000
POLL_DANCE_REINIT_CONTROLLERS_AFTER_NO_EVENT_TIME = 20000
POLL_DANCE_REINIT_CONTROLLERS_INTERVAL = 5000


# If no output change occurs for this many milliseconds, UI.poll() will start
# repeating REPEATABLE outputs that are being held. Mostly used so that you
# can hold an arrow to move through menus and don't have to constantly tap it.
REPEAT_INITIAL_DELAY = 250

# Delay between repeats after REPEAT_INITIAL_DELAY.
REPEAT_DELAY = 80

# Events that are repeatable by auto-repeat (see REPEAT_INITIAL_DELAY and poll())
REPEATABLE = frozenset(((-1,UP),(-1,DOWN),(-1,LEFT),(-1,RIGHT),
                         (0,UP),(0,DOWN),(0,LEFT),(0,RIGHT),
                         (1,UP),(1,DOWN),(1,LEFT),(1,RIGHT),
                         (2,UP),(2,DOWN),(2,LEFT),(2,RIGHT),
                         (3,UP),(3,DOWN),(3,LEFT),(3,RIGHT)))

# These events are prefixed with "P1_" or "P2_" in the config file and
# have a pid 0 or 1 associated with them when used in pydance input events.
dance_events = {
  "UP": UP,
  "DOWN": DOWN,
  "LEFT": LEFT,
  "RIGHT": RIGHT,
  "UPLEFT": UPLEFT,
  "UPRIGHT": UPRIGHT,
  "DOWNLEFT": DOWNLEFT,
  "DOWNRIGHT": DOWNRIGHT,
  "CENTER": CENTER,
}

# These events do not have a prefix in the config file and
# have a pid -1 associated with them when used in pydance input events.
# Note that "P1_UP" is not the same as "UP". On a dance pad, the up arrow
# would usually be configured to produce both at the same time, but a
# typical gamepad would have the d-pad produce only UP because the
# d-pad can't be used for dancing because you can't press up and down at
# the same time.
# Most pydance code does not need to care for this distinction because
# ui.poll() does not return dance events. Only the dancing code which
# uses ui.poll_dance() needs to care because that function returns
# everything.
control_events = {
  "CANCEL": CANCEL,
  "UP": UP,
  "DOWN": DOWN,
  "LEFT": LEFT,
  "RIGHT": RIGHT,
  "PGUP": PGUP,
  "PGDN": PGDN,
  "OPTIONS": OPTIONS,
  "RANDOM": RANDOM,
  "CONFIRM": CONFIRM,
  "FULLSCREEN": FULLSCREEN,
  "QUIT": QUIT,
  "SCREENSHOT": SCREENSHOT,
  "SORT": SORT,
}

def evstr(pid, evid):
  prefix = ""
  if evid < 0:
    evid = -evid
    prefix = "-"
  if evid >= GENERIC_BUTTON:
    prefix = ("C%d " % (pid >> GENERIC_BUTTON_SHIFTER)) + prefix
  if pid > 0: pid = pid & PLAYERS_MASK
  return "(%d,%s%s)" % (pid, prefix, EVNAMES[evid])


class EventValve(object):
  '''
  Produces events based on the crossing of a "pressure" threshold.
  '''
  def __init__(self, pid, evid, evlist, start_pressure):
    '''
    Creates an EventValve with value start_pressure. Whenever the
    pressure goes from 0 to 1 by use of the plus() method, a tuple
    (pid, evid) will be appended to evlist.
    Whenever the pressure goes from 1 to 0 by use of the minus() method,
    a tuple (pid, -evid) will be appended to evlist.

    There are 2 major applications:
    a) start_pressure == 0: The EventValve will function as an OR gate
                            for its inputs. It will be open if at least
                            one input is open.
    b) start_pressure == -N: The EventValve will function as an AND gate
                             for N+1 inputs. It will be closed if at least
                             one input is closed.

    '''
    self.open = (pid, evid)
    self.closed = (pid, -evid)
    self.evlist = evlist
    self.pressure = start_pressure
    self.start_pressure = start_pressure
    self.enabled = True

  def plus(self):
    if self.enabled and self.pressure == 0:
      self.evlist.append(self.open)
    self.pressure += 1

  def minus(self):
    self.pressure -= 1
    if self.enabled and self.pressure == 0:
      self.evlist.append(self.closed)

  def reset(self):
    ''' Set pressure to start pressure. '''
    self.pressure = self.start_pressure

  def visit(self, idx, state, visitor):
    visitor.visit([idx], [self])

  def __deepcopy__(self, memo): # never deepcopy self.evlist. It's a reference.
    return copy(self)

# the buffer to which EventValves created below append their output events.
# This is the default buffer for the UI class.
event_buffer = deque()

# maps a semantic event name as used in the config file (e.g. "P1_UP") to an EventValve for that event.
# ATTENTION! The EventValves in this dict must not be used directly. They have to be copied first, usually
# indirectly by EventPlumbing.clone().
valves = {}

# maps a (pid,evid) pair to an event name as used in the config file. (e.g. (0,UP) => "P1_UP")).
# This is basically the reverse of the valves dict.
valvenames = {}

# Create EventValves for all possible semantic events.
for evname, evid in control_events.iteritems():
  valves[evname] = EventValve(-1, evid, event_buffer, 0)
  valvenames[(-1, evid)] = evname
for pid in range(MAX_PLAYERS):
  for evname, evid in dance_events.iteritems():
    fullname = "P%d_%s" % (pid+1, evname)
    valves[fullname] = EventValve(pid, evid, event_buffer, 0)
    valvenames[(pid,evid)] = fullname
  for butt in range(MAX_BUTTONS):
    fullname = "P%d_BUTTON_%d" % (pid+1, butt)
    valves[fullname] = EventValve(pid, GENERIC_BUTTON+butt, event_buffer, 0)
    valvenames[(pid, GENERIC_BUTTON+butt)] = fullname


class EventForkValve(object):
  '''
  Passes on "pressure" to multiple EventValves based on the crossing of a "pressure" threshold.
  '''
  def __init__(self, start_pressure, output_valves):
    '''
    Creates an EventForkValve with value start_pressure. Whenever the
    pressure goes from 0 to 1 by use of the plus() method, the call x.plus() is
    performed for all x in output_valves.
    Whenever the pressure goes from 1 to 0 by use of the minus() method,
    the call x.minus() is performed for all x in output_valves.

    There are 2 major applications:
    a) start_pressure == 0: The EventForkValve will function as an OR gate
                            for its inputs. It will be open if at least
                            one input is open.
    b) start_pressure == -N: The EventForkValve will function as an AND gate
                             for N+1 inputs. It will be closed if at least
                             one input is closed.
    '''
    self.pressure = start_pressure
    self.start_pressure = start_pressure
    self.output_valves = output_valves

  def plus(self):
    if self.pressure == 0:
      for x in self.output_valves: x.plus()
    self.pressure += 1

  def minus(self):
    self.pressure -= 1
    if self.pressure == 0:
      for x in self.output_valves: x.minus()

  def reset(self):
    ''' Resets pressure to start pressure for this valve and all of its output valves. '''
    self.pressure = self.start_pressure
    for x in self.output_valves: x.reset()

  def visit(self, idx, state, visitor):
    lst = state.setdefault(self,[])
    lst.append(idx)
    if len(lst) == -self.start_pressure+1:
      visitor.visit(lst, self.output_valves)
      del state[self]

  def replace_valve(self, oldpidevid, newvalve, already_counted):
    i = 0
    have = False
    while i < len(self.output_valves):
      x = self.output_valves[i]
      if x.open == oldpidevid:
        self.output_valves[i] = newvalve
        if x not in already_counted:
          newvalve.pressure += x.pressure
          already_counted.add(x)

      if self.output_valves[i] == newvalve: # do not replace "self.output_valves[i]" with "x". It would be wrong!
        if have:
          del self.output_valves[i]
          i -= 1
        have = True
      i += 1

class PIDTransposer(object):
  def __init__(self, adder):
    self.adder = adder
    self.done = set()

  def visit(self, inputs, outputs):
    for valve in outputs:
      if valve not in self.done:
        self.done.add(valve)
        pid, evid = valve.open
        if pid >= 0:
          pid = (pid & ~PLAYERS_MASK) + ((pid + self.adder) & PLAYERS_MASK)
          valve.open = (pid, valve.open[1])
          valve.closed = (pid, valve.closed[1])

class MenuControlsEnabled(object):
  def __init__(self, onoff):
    self.onoff = onoff

  def visit(self, inputs, outputs):
    for valve in outputs:
      if valve.open[0] < 0:
        valve.enabled = self.onoff

class PlumbingStringer(object):
  '''
  Used with EventPlumbing.visit() to produce a string representation of
  the whole EventPlumbing. The result is found in the lines attribute.
  '''
  def __init__(self, is_keyboard):
    self.lines = []
    self.is_keyboard = is_keyboard

  def visit(self, inputs, outputs):
    input_strings = []
    for i in inputs:
      if i >= MAX_BUTTONS and not self.is_keyboard:
        input_strings.append(chr(ord('A')+(i-MAX_BUTTONS)/3) + ('-','0','+')[(i-MAX_BUTTONS)%3])
      else:
        input_strings.append(str(i))

    output_strings = []
    for valve in outputs:
      pid,evid = valve.open
      if pid > 0: pid = pid & PLAYERS_MASK
      if evid != PASS:
        if valve.enabled:
          output_strings.append(valvenames[(pid,evid)])
        else:
          output_strings.append('!'+valvenames[(pid,evid)])
    if len(output_strings) > 0 and len(input_strings) > 0:
      self.lines.append("%s = %s" % (" ".join(input_strings), " ".join(output_strings)))

class EventPlumbing(object):
  def __init__(self, container, blueprint):
    '''
    An EventPlumbing is a network of EventForkValves and EventValves. The entry point to
    the network are pipes with numeric indexes. Each index may have zero or more valves
    attached. These indexes correspond to actual buttons or keys or virtual buttons derived
    from controller axis states. Pressure for each index is controlled via the plus() and minus()
    methods. The pressure is passed on to the connected valves, causing them to open/close and
    finally producing event tuples (pid, evid) that are appended to a buffer.
    This constructor takes the endpoint valves from the ui.valves dict, so they determine
    which buffer events get appended to (usually ui.event_buffer).

    ATTENTION! An EventPlumbing constructed with this constructor should usually not be used
    directly. You need to clone() it first. Otherwise you would be pumping pressure into the
    valves from ui.valves which are shared with other EventPlumbings.

    container must be able to map ints to objects by use of "[]". If the container can not accept
    all indexes right away, it must support an append() method and len().
    Normally you would use
      {}  if the number of possible indexes is unreasonably high for a list.
      []  if the maximum index is reasonable.

    blueprint is the text representation of the plumbing. Syntax is as follows:

    <input1> ... = <output1> ...
    <input2> ... = <output2> ...
    ...

    where each <input> may be one of
      <uint>  a non-negative integer identifying a button on a controller or keyboard key
      L-, L0 or L+  where "L" is any letter from A-Z. This is a virtual button corresponding
                    to an axis state. The axis number is L-'A' (i.e. 'A' is axis 0) and the state
                    is value < -.5  for '-', value > .5 for '+' and between -.5 and .5 for '0'.
                    So at all times an axis will cause exactly one of these virtual buttons to
                    appear pressed.

      If multiple <input> appear on the same line, all of these buttons have to pressed at
      the same time in order to produce an output.

      If multiple <output> appear on the same line, all of these outputs will be produced at
      the same time. This is exactly identical to having an individual line for each output with
      the same <input>... list. The EventPlumbing constructor optimizes the network, so that it
      does not matter which form you use.
    '''

    # This is set based on the number of buttons occuring and is used when converting an
    # EventPlumbing to text. If is_keyboard == True, virtual button to axis name conversion is
    # disabled.
    self.is_keyboard = False

    # The name of the file to save changes to this plumbing in.
    self.filename = None

    # First line to output when saving to self.filename. Usually "[Controller Name]".
    self.header = ""

    # True => If this plumbing contains menuing events (e.g. CANCEL), disable them
    # when clone() is used to create a copy if this plumbing.
    self.menuing_disabled = False

    self.container = container
    for line in blueprint.splitlines():
      line = line.strip().upper()
      if line == "": continue

      input,unused,output = line.partition("=")
      if output == "":
        raise SyntaxError(_("Missing '=' or empty right side in \"%s\"") % (line,))

      if input == "":
        raise SyntaxError(_("Empty left side in \"%s\"") % (line,))

      inputset = set()
      for iword in input.split():
        if iword.isdigit(): # button number
          i = int(iword)
          if i >= MAX_BUTTONS:
            self.is_keyboard = True
          inputset.add(i)
        else: # virtual button derived from axis
          i = ord(iword[0])
          if len(iword) != 2 or iword[1] not in ('-','0','+') or i < ord("A") or i > ord("Z"):
            raise SyntaxError(_("Invalid axis state descriptor: \"%s\"") % (iword,))

          i = (i - ord("A")) * 3
          if iword[1] == "0":
            i += 1
          elif iword[1] == "+":
            i += 2

          inputset.add(MAX_BUTTONS+i)

      for oword in output.split():
        if oword[0] == "!":
          oword = oword[1:]
          # At this time we support only disabling of menu commands, so if we see any disabled
          # output even we simply assume that this is what's happening.
          self.menuing_disabled = True

        self.add(inputset, oword)

  def add(self, inputs, valvename):
    '''
    Adds a connection from inputs to valves[valvename].

    inputs may either be a single non-negative int or any iterable of
    non-negative ints.
    All of the buttons identified by inputs must be pressed simultaneously
    to activate the valve.

    Raises SyntaxError if valvename is not valid.

    WARNING! This function must not be used on an EventPlumbing whose plus()/minus()
    are being used. It must only be used on an EventPlumbing that will serve as a
    template to be duplicated by using the clone() method.

    NOTE! Optimizations are performed so that adding the same connection multiple
    times does not bloat the network. Nor does the order of add() calls influence
    the resulting network.
    '''
    try:
      valve = valves[valvename]
    except KeyError:
      raise SyntaxError(_("Invalid event name \"%s\"") % (valvename,))

    if hasattr(inputs, "__len__") and len(inputs) == 1:
      inputs = list(inputs)[0]

    if type(inputs) == int: # single button input => map directly to EventValve
      try:
        for v in self.container[inputs]:
          if type(v) == EventValve and v.open == valve.open:
            break # inputs -> valvename connection already exists
        else:
          raise KeyError

      # no existing connection found => make a new one
      except (KeyError,IndexError):
        self._make_index_valid(inputs)
        self.container[inputs].append(valve)

    else: # multi-button input => need to use EventForkValve
      try:
        # convert the lists (of EventForkValves and EventValves) for all inputs into sets
        l = [set(self.container[i]) for i in inputs]
        # create the intersection of all these sets, i.e. the set of all EventForkValves and EventValves
        # that are fed into by all of the inputs
        for s in l[1:]:
          l[0] &= s

        # now check if the intersection contains an EventForkValve with exactly len(inputs) inputs
        # (determined by checking start_pressure). If such an EFV is found, it follows that its
        # inputs are exactly the indexes from inputs, no more, no less.
        for v in l[0]:
          if type(v) == EventForkValve and v.start_pressure == -len(inputs)+1:
            # Append the target valve to the output list if its not already there.
            if valve.open not in [o.open for o in v.output_valves]:
              v.output_valves.append(valve)
            break
        else:
          raise KeyError

      # no existing connection found => make a new one
      except (KeyError,IndexError):
        for i in inputs:
          self._make_index_valid(i)

        fork = EventForkValve(-len(inputs)+1, [valve])
        for i in inputs:
          self.container[i].append(fork)

  def plus(self, index):
    try:
      for fork in self.container[index]:
        fork.plus()
    except (KeyError,IndexError):
      pass

  def minus(self, index):
    try:
      for fork in self.container[index]:
        fork.minus()
    except (KeyError,IndexError):
      pass

  def reset(self):
    '''
    Resets all valves in the network to their starting pressure.
    ATTENTION! Usually you would also want to clear the output buffer (ui.event_buffer)
    when you call this function .
    '''
    try:
      iter = self.container.itervalues()
    except AttributeError:
      iter = self.container

    for lst in iter:
      for x in lst:
        x.reset()

  def transpose_player(self, adder):
    '''Adds adder to all EventValves' pid >=0 , wrapping around at MAX_PLAYERS.'''
    self.visit(PIDTransposer(adder))

  def menu_controls_enabled(self, onoff):
    ''' Enables (onoff=True) or disables (onoff=False) all EventValves with pid < 0. '''
    self.menuing_disabled = True
    self.visit(MenuControlsEnabled(onoff))

  def clone(self):
    '''
    Creates an independent copy of this valve network. In particular it makes copies of the
    output valves that have been taken directly from ui.valves by the constructor.
    It is necessary to clone() an EventPlumbing before using its plus()/minus().
    '''
    c = deepcopy(self)
    if self.menuing_disabled: c.menu_controls_enabled(False)
    return c

  def save_to_disk(self):
    '''If this plumbing has a filename set and mainconfig["saveinput"], this plumbing is stored to disk.'''
    if self.filename is not None and mainconfig["saveinput"]:
      try:
        with open(os.path.join(input_d_path, self.filename), "w") as f:
          f.write(self.header)
          f.write("\n")
          f.write(repr(self))
      except:
        print _("W: Unable to write input configuration file %s") % (self.filename,)
        print sys.exc_info()[1]

  def visit(self, visitor):
    '''
    For every inputs => outputs connection, calls visitor.visit(input_list, output_list)
    where input_list is a list of ints and output_list is a list of EventValves.
    '''
    state = {}
    try:
      iter = self.container.keys()
      iter.sort()
    except AttributeError:
      iter = range(len(self.container))

    for idx in iter:
      for x in self.container[idx]:
        x.visit(idx, state, visitor)

  def replace_valve(self, oldpidevid, newvalve):
    '''
    Every EventValve in this plumbing whose open value is oldpidevid will be replaced
    with newvalve. Note that this means replacing the actual object pointer. The pressure
    values of all replaced valves will be summed up and added to newvalve.
    '''
    try:
      iter = self.container.keys()
    except AttributeError:
      iter = range(len(self.container))

    already_counted = set()

    for idx in iter:
      k = 0
      have = False
      while k < len(self.container[idx]):
        x = self.container[idx][k]
        if type(x) == EventValve:
          if x.open == oldpidevid:
            self.container[idx][k] = newvalve
            if x not in already_counted:
              newvalve.pressure += x.pressure
              already_counted.add(x)
          if self.container[idx][k] == newvalve: # DO NOT REPLACE "self.container[idx][k]" WITH "x". It would be wrong!
            if have:
              del self.container[idx][k]
              k -= 1
            have = True
        else:
          x.replace_valve(oldpidevid, newvalve, already_counted)

        k += 1

  def __repr__(self):
     pbs = PlumbingStringer(self.is_keyboard)
     self.visit(pbs)
     return "\n".join(pbs.lines)

  def _make_index_valid(self, idx):
    if hasattr(self.container,"append"): # container is a list or similar
      # make sure index idx is valid
      while len(self.container) <= idx: self.container.append([])
    else: # container is a dict or similar
      self.container.setdefault(idx,[])


# The numbers are the .key property of the pygame.event.
# The event names are those from the valves dict (defined further above).
default_keyboard_plumbing = EventPlumbing([], '''
13  = CONFIRM
271 = CONFIRM
9  = RANDOM
282  = OPTIONS
292  = FULLSCREEN
263  = P1_UPLEFT
265  = P1_UPRIGHT
261  = P1_CENTER
257  = P1_DOWNLEFT
259  = P1_DOWNRIGHT
260  = LEFT P1_LEFT
276 = LEFT P1_LEFT
262  = RIGHT P1_RIGHT
275 = RIGHT P1_RIGHT
258  = DOWN P1_DOWN
274 = DOWN P1_DOWN
264 = UP P1_UP
273 = UP P1_UP
8   = CANCEL
27   = CANCEL
280 = PGUP
281 = PGDN
305 113 = QUIT
305 120 = QUIT
306 113 = QUIT
306 120 = QUIT
308 113 = QUIT
308 120 = QUIT
313 113 = QUIT
313 120 = QUIT
316 = SCREENSHOT
277 = SORT
''')


 # Most gamepads seem to have Start on a button >=6 (based on https://raw.githubusercontent.com/gabomdq/SDL_GameControllerDB/master/gamecontrollerdb.txt).
 # with an odd number (1st place 9, 2nd place 7, 3rd place 11). We default all odd numbered
 # buttons >6 to OPTIONS and the rest to CANCEL. Because OPTIONS works as CONFIRM in most places
 # this allows players to navigate the game without button mappings right away.
default_controller_plumbing = EventPlumbing([], '''
A- = LEFT P1_LEFT
A+ = RIGHT P1_RIGHT
B- = UP P1_UP
B+ = DOWN P1_DOWN

0 = P1_BUTTON_0
1 = P1_BUTTON_1
2 = P1_BUTTON_2
3 = P1_BUTTON_3
4 = P1_BUTTON_4
5 = P1_BUTTON_5
6 = CANCEL
7 = OPTIONS
8 = CANCEL
9 = OPTIONS
10 = CANCEL
11 = OPTIONS
12 = CANCEL
13 = OPTIONS
14 = CANCEL
15 = OPTIONS
16 = CANCEL
17 = OPTIONS
18 = CANCEL
19 = OPTIONS
20 = CANCEL
21 = OPTIONS
22 = CANCEL
23 = OPTIONS
24 = CANCEL
25 = OPTIONS
26 = CANCEL
27 = OPTIONS
28 = CANCEL
29 = OPTIONS
30 = CANCEL
31 = OPTIONS
''')

# Maps a pygame.joystick.Joystick.get_name() to a list L of EventPlumbing objects, where
# L[i] is the plumbing to be used for the i-th controller of that name attached to the
# system. Some L[i] may be None. In that case the plumbing will be derived through transposition.
plumbing_templates = {}

def read_plumbing_templates():
  if os.path.exists(input_d_path):
    try:
      lst = os.listdir(input_d_path)
      lst.sort()
    except OSError:
      lst = []

    for fn in lst:
      if fn.endswith(".cfg"):
        try:
          inp = open(os.path.join(input_d_path,fn), "rU").read(-1)
          start = inp.find("[")
          if start < 0: raise SyntaxError(_("'[' not found"))
          end = inp.find("]", start)
          if end < 0: raise SyntaxError(_("']' not found"))
          controller_name = inp[start+1:end].strip()
          hash = controller_name.rfind("#")
          controller_index = 0
          if hash > 0 and controller_name[hash+1:].isdigit():
            controller_index = int(controller_name[hash+1:])
            controller_name = controller_name[:hash].strip()

          pb = EventPlumbing([], inp[end+1:])
          print (_("Loaded %s") % (fn,))
          pb.filename = fn

          if controller_index > 0:
            pb.header = "[%s #%d]" % (controller_name, controller_index)
          else:
            pb.header = "[%s]" % (controller_name,)

          pbtlst = plumbing_templates.setdefault(controller_name,[])
          while controller_index >= len(pbtlst):
            pbtlst.append(None)

          pbtlst[controller_index] = pb

        except:
          print _("W: Unable to load input configuration file %s") % (fn,)
          print sys.exc_info()[1]

def get_plumbing(name, idx):
  if name in plumbing_templates:
    lst = plumbing_templates[name]
    i = idx
    while i > 0 and (i >= len(lst) or lst[i] is None): i -= 1
    if lst[i] is None: # implies i == 0
      i = idx
      while lst[i] is None: i += 1

    pb = lst[i].clone()

    if i != idx:
      print(_("Transposing %s to get mapping for %s #%d") % (pb.header, name, idx))
      pb.transpose_player(idx - i)
      pb.menu_controls_enabled(False)  # don't want player 2 to control menu

      assert pb.filename.endswith(".cfg")
      pb.filename = pb.filename[:-4]
      underscore = pb.filename.rfind("_")
      if underscore >= 0:
        pb.filename = pb.filename[:underscore]
      if idx != 0:
        pb.filename += "_%d" % (idx,)
      pb.filename += ".cfg"

      if pb.filename[0:2].isdigit() and pb.filename[2] == "-":
        pb.filename = pb.filename[3:]

      pb.filename = "10-" + pb.filename

      hash = pb.header.rfind("#")
      if hash < 0:
        hash = len(pb.header)-1 # point to "]"

      pb.header = pb.header[:hash] + (" #%d]" % (idx,) )
    else:
      print(_("Using mapping for %s #%d") % (name, idx))

  else:
    if name == "keyboard":
      pb = default_keyboard_plumbing.clone()
      pb.filename = "10-keyboard.cfg"
      pb.header = "[keyboard]"

    else:
      print(_("Using default mapping for %s #%d") % (name, idx))
      pb = default_controller_plumbing.clone()
      pb.filename = "10-" + "".join(ch.lower() for ch in name if ch.isalnum())
      pb.header = "[" + name
      if idx > 0:
        pb.filename += "_%d" % (idx,)
        pb.header += " #%d" % (idx,)
        pb.transpose_player(idx)
        pb.menu_controls_enabled(False)  # don't want player 2 to control menu

      pb.filename += ".cfg"
      pb.header += "]"

  return pb

class GenericButtonsFixup(object):
  '''
  Embeds joy << GENERIC_BUTTON_SHIFTER in the pid of every EventValve's output event that refers to a
  event id >= GENERIC_BUTTON.
  '''
  def __init__(self, joy):
    self.fixup = joy << GENERIC_BUTTON_SHIFTER

  def visit(self, inputs, outputs):
    for valve in outputs:
      if valve.open[1] >= GENERIC_BUTTON:
        valve.open = ((valve.open[0] & PLAYERS_MASK)+self.fixup, valve.open[1])
        valve.closed = ((valve.closed[0] & PLAYERS_MASK)+self.fixup, valve.closed[1])

class ReplaceEvent(object):
  def __init__(self, oldpidevid, pid, evid):
    self.oldpidevid = oldpidevid
    self.pid = pid
    self.evid = evid

  def visit(self, inputs, outputs):
    for valve in outputs:
      if valve.open == self.oldpidevid:
        valve.open = (self.pid, self.evid)
        valve.closed = (self.pid, -self.evid)

class DisableEvent(object):
  def __init__(self, pid, evid):
    self.pidevid = (pid, evid)

  def visit(self, inputs, outputs):
    for valve in outputs:
      if valve.open == self.pidevid:
        valve.enabled = False

class RepeatEvent(object):
  def __init__(self):
    self.already_done = set()

  def visit(self, inputs, outputs):
    for valve in outputs:
      if valve.pressure > 0 and valve.open in REPEATABLE and valve not in self.already_done:
        valve.evlist.append(valve.open)
        self.already_done.add(valve)

class CountOpenValves(object):
  def __init__(self):
    self.already_done = set()
    self.count = 0

  def visit(self, inputs, outputs):
    for valve in outputs:
      if valve not in self.already_done:
        self.already_done.add(valve)
        if valve.pressure > 0:
          self.count += 1


class FindEvent(object):
  def __init__(self, pid, evid):
    self.valve = None
    self.ev = (pid,evid)

  def visit(self, inputs, outputs):
    for valve in outputs:
      if valve.open == self.ev:
        self.valve = valve
        break


class DebugValves(object):
  def visit(self, inputs, outputs):
    for valve in outputs:
      if valve.pressure != valve.start_pressure:
        print("%s %s" % (id(valve),valve.__dict__))

class Controller(object):
  def __init__(self, pygame):
    self.pygame = pygame
    self.plumbing = None
    self.num_hats = pygame.get_numhats()
    self.button_state = [False] * MAX_BUTTONS
    self.num_pressed = 0 # number of buttons currently in pressed state
    # axis_state is a list of N booleans corresponding to the virtual buttons derived from
    # the axes. The list always starts with states derived from hats. Each
    # hat generates 6 axis states: x < 0, x == 0, x > 0, y < 0, y == 0, y > 0
    # This is followed by the axis states for the analog axes. Each analog axis generates
    # 3 axis states: a < -.5, a in [-.5,.5], a > .5
    self.axis_state = []
    for h in range(self.num_hats):
      self.axis_state.extend((False,False,False,False,False,False))
    for a in range(pygame.get_numaxes()):
      self.axis_state.extend((False,False,False))
    # Used for learning the relationship (if any) of generic buttons with directional axes.
    # for button >= GENERIC_BUTTON: generic_buttons[button] = [total, left, right, up, down]
    # where
    #   total is the total number of times this button has been seen in the event_buffer
    #   up is the number of times (pid, UP) was seen at the same time in the event_buffer
    #   ditto for left,right,down
    self.generic_buttons = {}


  def pressed(self, button):
    if not self.button_state[button]:
      self.button_state[button] = True
      self.plumbing.plus(button)
      self.num_pressed += 1

  def released(self, button):
    if self.button_state[button]:
      self.button_state[button] = False
      self.plumbing.minus(button)
      self.num_pressed -= 1

  def reset(self):
    for i in range(len(self.button_state)):
      self.button_state[i] = False
    for i in range(len(self.axis_state)):
      self.axis_state[i] = False
    self.generic_buttons = {}
    self.num_pressed = 0

class UI(object):
  '''
  Receives raw user input from pygame.event and translates
  it into higher level semantic events used by pydance. The translation is highly
  configurable and permits things such as translating 2 simultaneous button presses
  into a ui.SCREENSHOT event. See EventPlumbing.
  '''
  def __init__(self, ebuf = event_buffer):
    # list of lists of pygame.event.Event objects, one for each call of pump().
    self.pygame_events = deque()
    # sum of the lengths of all lists in pygame_events.
    self.pygame_events_count = 0

    # stores tuples (pid, evid) or (pid, -evid) for button presses/releases.
    # Used as FIFO. EventValves append data. poll() and poll_dance() remove it.
    self.event_buffer = ebuf

    # this logs the last time an output even was produced in pump()
    self.last_valve_change_time = pygame.time.get_ticks()

    # this logs the last time, a non-empty event list was received from pygame
    self.last_pygame_events_time = pygame.time.get_ticks()

    # this is the earliest time at which poll() will trigger an auto-repeat.
    self.next_repeat_time = pygame.time.get_ticks()

    # the pressed/released state of every keyboard key by pygame.Event.key number.
    self.key_state = [False] * 512

    # the EventPlumbing used for keyboard inputs.
    self.keyboard_plumbing = get_plumbing("keyboard", 0)

    # list of Controller objects. Array index matches pygame's joystick id.
    self.controllers = []

    # controller_names[i] is controllers[i].pygame.get_name()
    self.controller_names = []

    # Used by _can_skip_init_controllers()
    self._init_controllers_state = {}

    self.learn_sound = pygame.mixer.Sound(os.path.join(sound_path, "assist-l.ogg"))
    self.learn_sound.set_volume(.345)
    self.plug_in_sound = pygame.mixer.Sound(os.path.join(sound_path, "clicked.ogg"))
    self.plug_in_sound.set_volume(.345)
    self.pull_out_sound = pygame.mixer.Sound(os.path.join(sound_path, "back.ogg"))
    self.pull_out_sound.set_volume(.345)

    self.init_controllers()

  def force_init_controllers(self):
    '''Forces reinitialisation of the event subsystem.'''
    self.controllers = []
    self.controller_names = []
    self._init_controllers_state = {}
    self.init_controllers()

  def init_controllers(self):
    if self._can_skip_init_controllers():
      return

    pygame.joystick.quit() # turn off joystick module to make sure following init() rescans for controllers
    pygame.joystick.init()
    controller_names = []
    sticks = []
    for joy in range(pygame.joystick.get_count()):
      stick = pygame.joystick.Joystick(joy)
      stick.init()
      sticks.append(stick)
      controller_names.append(stick.get_name())

    if controller_names != self.controller_names:
      if len(controller_names) < len(self.controller_names):
        self.pull_out_sound.play()
      else:
        self.plug_in_sound.play()

      controllers = []
      i = 0
      while i < len(controller_names):
        name = controller_names[i]
        idx = 0
        k = i - 1
        while k >= 0:
          if controller_names[k] == name:
            idx += 1
          k -= 1

        # idx is the number of controllers of the same name that precede this controller.
        # We assume that if multiple controllers have the same name, they are used by
        # player 1, player 2,...
        # Controllers with different names are all assigned to player 1 by default, because
        # we have no way of knowing which should be player 2. Using the pygame joystick index
        # for this is a bad idea because
        # a) it changes based on the order controllers a plugged into USB ports
        # b) if 1 game pad and 1 dance pad are connected, it's usually not for 2 player games

        idx2 = 0
        k = 0
        joy = len(controllers)
        while k < len(self.controller_names):
          if self.controller_names[k] == name:
            if idx2 == idx: # found a corresponding entry in self.controller_names/self.controllers
              self.controllers[k].pygame = sticks[i]  # update the pygame object
              controllers.append(self.controllers[k]) # reuse the rest (in particular the plumbing)
              break
            idx2 += 1
          k += 1
        else: # if the previous while did not find a corresponding old entry => create new one
          controllers.append(Controller(sticks[i]))
          controllers[joy].plumbing = get_plumbing(name, idx)

        controllers[joy].plumbing.visit(GenericButtonsFixup(joy))
        i += 1
      # end while

      self.controllers = controllers
      self.controller_names = controller_names

  def set_keyboard_plumbing(self, kb):
    self.keyboard_plumbing = kb
    self.key_state = [False] * 512 # must reset key state or plumbing's valves don't match

  def set_controller_plumbing(self, joy, pb):
    self.controllers[joy].plumbing = pb
    self.controllers[joy].plumbing.visit(GenericButtonsFixup(joy))
    self.controllers[joy].reset()

  def poll(self, autorepeat = True, reinit_time = POLL_REINIT_CONTROLLERS_AFTER_NO_EVENT_TIME, reinit_interval = POLL_REINIT_CONTROLLERS_INTERVAL):
    '''
    Returns a pair (pid, evid) or (pid, -evid) where
      * pid is the player id the event belongs to (0..MAX_PLAYERS-1) or -1 if it is
        a non-player specific event (menuing).
        NOTE: Directions like UP may create 2 events, one with pid < 0 and one with a
        pid >= 0. Make sure you always evaluate pid when checking for directions.
      * evid is the event id (e.g. CONFIRM). It is positive when the event is asserted
        and negative when the event is de-asserted.

    The special evid 0 (==PASS) is returned with pid<0 when no event is pending.

    If autorepeat == True and no events occur for REPEAT_INITIAL_DELAY, outputs that
    are being held and are in the REPEATABLE set will be repeated every REPEAT_DELAY ms.

    During dancing, the special function poll_dance() is used instead of this one.
    '''
    ticks = pygame.time.get_ticks()

    if len(self.event_buffer) == 0:
      events = pygame.event.get()
      if len(events) > 0:
        self.last_pygame_events_time = ticks
        self.pump(events)
      else:
        if ticks > self.last_pygame_events_time + reinit_time:
          self.last_pygame_events_time = ticks - reinit_time + reinit_interval
          self.init_controllers()

    # discard generic button events
    while len(self.event_buffer) > 0 and (
          self.event_buffer[0][1] >= GENERIC_BUTTON or self.event_buffer[0][1] <= -GENERIC_BUTTON):
      self.event_buffer.popleft()

    if len(self.event_buffer) == 0:
      if autorepeat and ticks > self.last_valve_change_time + REPEAT_INITIAL_DELAY:
        if ticks > self.next_repeat_time:
          self.next_repeat_time = ticks + REPEAT_DELAY
          self.repeat_output()

      if len(self.event_buffer) == 0:
        return (-1, PASS)

    return self.event_buffer.popleft()

  def poll_dance(self):
    '''
    Similar to poll() but filters out some events you don't want during the dance part.
    In particular it does not have any auto-repeat functionality.
    '''
    return self.poll(False, POLL_DANCE_REINIT_CONTROLLERS_AFTER_NO_EVENT_TIME, POLL_DANCE_REINIT_CONTROLLERS_INTERVAL)

  def repeat_output(self):
    '''
    All currently open valves will put a copy of their open event into the event queue.
    Note that this will cause a discrepancy between press and release events, so this
    function must not be used in a context that tracks state (such as the dance loop,
    which is why it uses poll_dance() which does not use auto-repeating).
    '''
    self.keyboard_plumbing.visit(RepeatEvent())
    for joy in range(len(self.controllers)):
      self.controllers[joy].plumbing.visit(RepeatEvent())

  def count_open_valves(self):
    ''' Returns the number of currently open output valves, i.e. asserted events.'''
    count = CountOpenValves()
    for joy in range(len(self.controllers)):
      self.controllers[joy].plumbing.visit(count)
    return count.count

  def count_valves(self, joy):
    ''' Returns the total number of output valves for controller joy.'''
    count = CountOpenValves()
    self.controllers[joy].plumbing.visit(count)
    return len(count.already_done)

  def clear(self, max_wait = 500):
    '''
    Removes all events from the event queue and waits until no button or direction is pressed,
    but a maximum time of max_wait ms.
    '''
    end_time = pygame.time.get_ticks() + max_wait
    while True:
      if self.poll()[1] == PASS:
        if self.count_open_valves() == 0:
          return
        if pygame.time.get_ticks() > end_time:
          return
        pygame.time.wait(20)

  def wait(self, delay = 20):
    '''Like poll() but if no input is available, waits with poll interval delay ms until an input
    is available and returns it.'''
    while True:
      ev = self.poll()
      if ev[1] != PASS: return ev
      pygame.time.wait(delay)

  def pump(self, events):
    '''Process list of events (of type pygame.event.Event) and move the results into our own queue.'''
    self.pygame_events.append(events)
    self.pygame_events_count += len(events)
    while self.pygame_events_count > MAX_KEEP_PYGAME_EVENTS:
      self.pygame_events_count -= len(self.pygame_events.popleft())

    num_events = len(self.event_buffer)
    for event in events:
      if event.type == pygame.QUIT:
        self.event_buffer.append((-1,QUIT))
      elif event.type == pygame.KEYDOWN:
        if event.key < len(self.key_state) and not self.key_state[event.key]:
          self.key_state[event.key] = True
          self.keyboard_plumbing.plus(event.key)
      elif event.type == pygame.KEYUP:
        if event.key < len(self.key_state) and self.key_state[event.key]:
          self.key_state[event.key] = False
          self.keyboard_plumbing.minus(event.key)
      elif event.type == pygame.JOYBUTTONDOWN:
        if event.button < MAX_BUTTONS:  # we use numbers >= MAX_BUTTONS for axes
          self.controllers[event.joy].pressed(event.button)
      elif event.type == pygame.JOYBUTTONUP:
        if event.button < MAX_BUTTONS:  # we use numbers >= MAX_BUTTONS for axes
          self.controllers[event.joy].released(event.button)
      elif event.type == pygame.JOYHATMOTION:
        axis = MAX_BUTTONS + event.hat * 6
        x,y = event.value
        self._handle_axis(event.joy, axis, x < 0)
        self._handle_axis(event.joy, axis+1, x == 0)
        self._handle_axis(event.joy, axis+2, x > 0)
        self._handle_axis(event.joy, axis+3, y > 0)
        self._handle_axis(event.joy, axis+4, y == 0)
        self._handle_axis(event.joy, axis+5, y < 0)
      elif event.type == pygame.JOYAXISMOTION:
        axis = MAX_BUTTONS + self.controllers[event.joy].num_hats * 6 + event.axis * 3
        a = event.value
        self._handle_axis(event.joy, axis, a < -.5)
        self._handle_axis(event.joy, axis+1, a >= -.5 and a <= .5)
        self._handle_axis(event.joy, axis+2, a > .5)

    if len(self.event_buffer) > num_events:
      self.last_valve_change_time = pygame.time.get_ticks()
      self._handle_generic_buttons(num_events)

  def _handle_axis(self, joy, axis, state):
    '''
    Update state (boolean) of axis for controller joy and pump into plumbing if it changed.
    '''
    if self.controllers[joy].axis_state[axis-MAX_BUTTONS] != state:
      self.controllers[joy].axis_state[axis-MAX_BUTTONS] = state
      if state:
        self.controllers[joy].plumbing.plus(axis)
      else:
        self.controllers[joy].plumbing.minus(axis)

  def _handle_generic_buttons(self, num_events):
    '''
    Goes through self.event_buffer[num_events:] and learns if certain generic buttons
    are tied to directional axes.
    '''
    active = {}
    generic = []

    for i in range(num_events, len(self.event_buffer)):
      pid, evid = self.event_buffer[i]
      if pid >= 0:
        if evid in (LEFT,RIGHT,UP,DOWN):
          active[(pid,evid)] = 1
        elif evid >= GENERIC_BUTTON:
          joy = pid >> GENERIC_BUTTON_SHIFTER
          if self.controllers[joy].num_pressed == 1: # only exactly this generic button is pressed
            generic.append((pid,evid))

    for (pid,evid) in generic:
      joy = pid >> GENERIC_BUTTON_SHIFTER
      pid = pid & PLAYERS_MASK
      gen = self.controllers[joy].generic_buttons.setdefault(evid,[0,0,0,0,0])
      gen[0] += 1
      gen[1] += active.get((pid,LEFT),0)
      gen[2] += active.get((pid,RIGHT),0)
      gen[3] += active.get((pid,UP),0)
      gen[4] += active.get((pid,DOWN),0)

      if gen[0] > MIN_LEARN_GENERIC_BUTTON_COUNT:
        indep = 0
        count = float(gen[0])
        for i in (1,2,3,4):
          p = gen[i]/count
          if p > LEARN_GENERIC_BUTTON_DIRECTION:
            self._learn_generic_button(joy, pid, evid, pid, (LEFT,RIGHT,UP,DOWN)[i-1])
            break
          elif p < LEARN_GENERIC_BUTTON_INDEPENDENT:
            indep += 1

        if indep == 4:
          self._learn_generic_button(joy, pid, evid, -1, PASS)

  def _learn_generic_button(self, joy, oldpid, oldevid, pid, evid):
    self.learn_sound.play()
    found = FindEvent(pid,evid)
    self.controllers[joy].plumbing.visit(found)
    if found.valve is not None:
      self.controllers[joy].plumbing.replace_valve((oldpid + (joy << GENERIC_BUTTON_SHIFTER), oldevid),found.valve)
    else:
      self.controllers[joy].plumbing.visit(ReplaceEvent((oldpid + (joy << GENERIC_BUTTON_SHIFTER), oldevid), pid, evid))
    del self.controllers[joy].generic_buttons[oldevid]
    self.controllers[joy].plumbing.save_to_disk()

  def _can_skip_init_controllers(self):
    ''' Because reiniting controllers is slow, this function implements OS dependent
    shortcuts do determine if a reinit is necessary. Returns False if we know we
    can skip the init.'''

    # At this time we only have a shortcut for Linux. We simply check common directories
    # for the appearing and disappearing of device nodes.
    skip = True
    count = 0
    for d in ("/dev/input", "/dev/input/by-id"):
      try:
        lst = os.listdir(d)
        lst.sort()
      except OSError:
        lst = []

      count += len(lst)

      if d not in self._init_controllers_state or self._init_controllers_state[d] != lst:
        self._init_controllers_state[d] = lst
        skip = False

    return skip and count > 0 # the count > 0 check tests if the directories did even exist


read_plumbing_templates()
ui = UI()


####################################################################################################
#
#     ALL CODE BELOW THIS LINE IS TEST CODE THAT'S NEVER EXECUTED BY PYDANCE
#
####################################################################################################
if __name__ == "__main__":
  size = width, height = 300, 300
  screen = pygame.display.set_mode(size)

  ########## EventValve unit tests #########
  valve = valves["P1_UP"]
  assert len(event_buffer) == 0
  assert valvenames[valve.open] == "P1_UP"
  valve.plus()
  assert len(event_buffer) == 1 and event_buffer.pop() == (0, UP)
  valve.plus()
  assert len(event_buffer) == 0
  valve.minus()
  assert len(event_buffer) == 0
  valve.minus()
  assert len(event_buffer) == 1 and event_buffer.pop() == (0, -UP)
  valve.plus()
  assert len(event_buffer) == 1 and event_buffer.pop() == (0, UP)
  valve.reset() # make sure we don't mess up future tests that happen to use valves["P1_UP"]

  valve = deepcopy(EventValve(-2, 42, event_buffer, -2))
  assert len(event_buffer) == 0
  valve.plus()
  assert len(event_buffer) == 0
  valve.plus()
  assert len(event_buffer) == 0
  valve.plus()
  assert len(event_buffer) == 1 and event_buffer[0] == (-2, 42)
  valve.minus()
  assert len(event_buffer) == 2 and event_buffer[0] == (-2, 42) and event_buffer[1] == (-2, -42)
  valve.reset()
  event_buffer.clear()
  assert len(event_buffer) == 0
  valve.plus()
  assert len(event_buffer) == 0
  valve.plus()
  assert len(event_buffer) == 0
  valve.plus()
  assert len(event_buffer) == 1 and event_buffer.pop() == (-2, 42)

  ######### EventForkValve unit tests ###########
  v1 = EventValve(666, 1, event_buffer, -2)
  v2 = EventValve(42, 2, event_buffer, -1)
  v3 = EventValve(69, 3, event_buffer, 0)
  v4 = EventValve(77, 4, event_buffer, 0)
  fork1 = EventForkValve(0, [v1,v2,v4])
  fork2 = EventForkValve(-1, [v1,v2,v4])
  fork3 = EventForkValve(-2, [v1,v4])
  fork3.replace_valve((77,4), v3, set())
  fork2.replace_valve((77,4), v3, set())
  fork1.replace_valve((77,4), v3, set())
  assert v4.open != v3.open
  assert len(event_buffer) == 0
  fork3.plus()
  fork2.plus()
  assert len(event_buffer) == 0
  fork1.plus()
  assert len(event_buffer) == 1 and event_buffer.pop() == (69, 3)
  fork1.plus()
  assert len(event_buffer) == 0
  fork1.minus()
  assert len(event_buffer) == 0
  fork2.plus()
  assert len(event_buffer) == 1 and event_buffer.pop() == (42, 2)
  fork3.plus()
  assert len(event_buffer) == 0
  fork3.plus()
  assert len(event_buffer) == 1 and event_buffer.pop() == (666, 1)
  fork1.minus()
  assert len(event_buffer) == 2 and event_buffer.popleft() == (666, -1) and event_buffer.pop() == (42, -2)
  fork2.minus()
  fork3.minus()
  assert len(event_buffer) == 1 and event_buffer.pop() == (69, -3)
  assert len(event_buffer) == 0
  fork1.plus()
  fork2.plus()
  fork3.plus()
  event_buffer.clear()
  fork1.reset()
  fork2.reset()
  fork3.reset()
  assert len(event_buffer) == 0
  fork3.plus()
  fork2.plus()
  assert len(event_buffer) == 0
  fork1.plus()
  assert len(event_buffer) == 1 and event_buffer.pop() == (69, 3)
  fork1.plus()
  assert len(event_buffer) == 0
  fork1.minus()
  assert len(event_buffer) == 0
  fork2.plus()
  assert len(event_buffer) == 1 and event_buffer.pop() == (42, 2)
  fork3.plus()
  assert len(event_buffer) == 0
  fork3.plus()
  assert len(event_buffer) == 1 and event_buffer.pop() == (666, 1)

  ############ EventPlumbing unit tests #########
  try:
    pb = EventPlumbing([],'=')
    assert 1 == 0
  except SyntaxError as x:
    print('"%s" => "%s"' % ("Missing '=' or empty right side in \"=\"",x))

  try:
    pb = EventPlumbing([],'= UP')
    assert 1 == 0
  except SyntaxError as x:
    print('"%s" => "%s"' % ("Empty left side in \"= UP\"",x))

  try:
    pb = EventPlumbing([],'0 = GORTZ')
    assert 1 == 0
  except SyntaxError as x:
    print('"%s" => "%s"' % ("Invalid event name \"GORTZ\"",x))

  lst = []
  pb = EventPlumbing(lst,'53 = QUIT')
  assert len(lst) == 54
  assert [i for i in lst if len(i) > 0] == [[valves["QUIT"]]]
  pb.add(53, "CANCEL")
  assert len(lst) == 54
  assert [i for i in lst if len(i) > 0] == [[valves["QUIT"],valves["CANCEL"]]]
  pb.add(53, "CANCEL")
  assert len(lst) == 54
  assert [i for i in lst if len(i) > 0] == [[valves["QUIT"],valves["CANCEL"]]]

  lst = {}
  pb = EventPlumbing(lst,'53 = QUIT')
  assert len(lst) == 1
  assert lst[53] == [valves["QUIT"]]
  pb.add(53, "CANCEL")
  assert len(lst) == 1
  assert lst[53] == [valves["QUIT"],valves["CANCEL"]]
  pb.add(53, "CANCEL")
  assert len(lst) == 1
  assert lst[53] == [valves["QUIT"],valves["CANCEL"]]

  lst = []
  pb = EventPlumbing(lst,'37 53 = QUIT')
  assert len(lst) == 54
  l = [i for i in lst if len(i) > 0]
  assert len(l) == 2
  assert l[0] == lst[37] and l[0] == lst[53] and l[0] == l[1]
  assert len(l[0]) == 1
  fork = l[0][0]
  assert type(fork) == EventForkValve
  assert fork.start_pressure == -1
  assert fork.output_valves == [valves["QUIT"]]

  pb.add([53,37], "CANCEL")
  assert len(lst) == 54
  l = [i for i in lst if len(i) > 0]
  assert len(l) == 2
  assert l[0] == lst[37] and l[0] == lst[53] and l[0] == l[1]
  assert len(l[0]) == 1
  fork = l[0][0]
  assert type(fork) == EventForkValve
  assert fork.start_pressure == -1
  assert fork.output_valves == [valves["QUIT"],valves["CANCEL"]]

  pb.add([53,37], "CANCEL")
  assert len(lst) == 54
  l = [i for i in lst if len(i) > 0]
  assert len(l) == 2
  assert l[0] == lst[37] and l[0] == lst[53] and l[0] == l[1]
  assert len(l[0]) == 1
  fork = l[0][0]
  assert type(fork) == EventForkValve
  assert fork.start_pressure == -1
  assert fork.output_valves == [valves["QUIT"],valves["CANCEL"]]

  pb.add([53,64], "QUIT")
  assert len(lst) == 65
  l = [i for i in lst if len(i) > 0]
  assert len(l) == 3
  assert len(lst[53]) == 2
  assert len(lst[64]) == 1
  assert lst[53][1] == lst[64][0]
  fork = lst[64][0]
  assert type(fork) == EventForkValve
  assert fork.start_pressure == -1
  assert fork.output_valves == [valves["QUIT"]]

  pb.add([37,53,64], "QUIT")
  pb.add([53,37], "CANCEL")
  assert len(lst) == 65
  l = [i for i in lst if len(i) > 0]
  assert len(l) == 3
  assert len(lst[37]) == 2
  assert len(lst[53]) == 3
  assert len(lst[64]) == 2
  fork = lst[64][1]
  assert type(fork) == EventForkValve
  assert fork.start_pressure == -2
  assert fork.output_valves == [valves["QUIT"]]

  lst = {}
  pb = EventPlumbing(lst,'37 53 = QUIT')
  assert len(lst) == 2
  assert lst[37] == lst[53]
  fork = lst[37][0]
  assert type(fork) == EventForkValve
  assert fork.start_pressure == -1
  assert fork.output_valves == [valves["QUIT"]]

  pb.add([53,37], "CANCEL")
  assert len(lst) == 2
  assert lst[37] == lst[53]
  fork = lst[37][0]
  assert type(fork) == EventForkValve
  assert fork.start_pressure == -1
  assert fork.output_valves == [valves["QUIT"],valves["CANCEL"]]

  pb.add([53,37], "CANCEL")
  assert len(lst) == 2
  assert lst[37] == lst[53]
  fork = lst[37][0]
  assert type(fork) == EventForkValve
  assert fork.start_pressure == -1
  assert fork.output_valves == [valves["QUIT"],valves["CANCEL"]]

  pb.add([53,64], "QUIT")
  assert len(lst) == 3
  assert len(lst[53]) == 2
  assert len(lst[64]) == 1
  assert lst[53][1] == lst[64][0]
  fork = lst[64][0]
  assert type(fork) == EventForkValve
  assert fork.start_pressure == -1
  assert fork.output_valves == [valves["QUIT"]]

  pb.add([37,53,64], "QUIT")
  pb.add([53,37], "CANCEL")
  assert len(lst) == 3
  assert len(lst[37]) == 2
  assert len(lst[53]) == 3
  assert len(lst[64]) == 2
  fork = lst[64][1]
  assert type(fork) == EventForkValve
  assert fork.start_pressure == -2
  assert fork.output_valves == [valves["QUIT"]]

  event_buffer.clear()
  pb.plus(37)
  assert len(event_buffer) == 0
  pb.plus(53)
  assert len(event_buffer) == 2 and event_buffer[0] == (-1, QUIT) and event_buffer[1] == (-1, CANCEL)
  pb.plus(64) # does NOT cause another QUIT to be output because QUIT valve is already open
  assert len(event_buffer) == 2
  event_buffer.clear()
  pb.minus(37)
  assert len(event_buffer) == 1 and event_buffer.pop() == (-1, -CANCEL)
  pb.reset()
  pb.plus(37)
  assert len(event_buffer) == 0
  pb.plus(53)
  assert len(event_buffer) == 2 and event_buffer[0] == (-1, QUIT) and event_buffer[1] == (-1, CANCEL)

  assert repr(EventPlumbing({},'1 2 = QUIT\nA0 = UP P1_UP')) == "1 2 = QUIT\nA0 = UP\nA0 = P1_UP"
  assert repr(EventPlumbing([],'1 2 = QUIT\nA- B+ = UP P1_UP')) == "1 2 = QUIT\nA- B+ = UP P1_UP"
  assert repr(EventPlumbing({},'49 2 = QUIT')) == "2 49 = QUIT"

  pb = EventPlumbing([],'0 = CANCEL\n1 2 3 = QUIT\n4 = QUIT\n0 = QUIT')
  pb.replace_valve((-1,QUIT), valves['P1_UP'])
  assert repr(pb) == '''0 = CANCEL
0 = P1_UP
1 2 3 = P1_UP
4 = P1_UP'''

  ######## UI unit tests ##########
  for v in valves: valves[v].reset()   # just in case on of the earlier tests has left a valve open
  event_buffer.clear()
  joymock = type('',(object,),{
    'get_name':lambda self: 'Mock Stick',
    'get_numhats': lambda self: 1,
    'get_numaxes': lambda self: 2,
  })()
  joymock2 = type('',(object,),{
    'get_name':lambda self: 'Mock Stick',
    'get_numhats': lambda self: 1,
    'get_numaxes': lambda self: 2,
  })()
  ui.controllers = [Controller(joymock), Controller(joymock2)]

  pb = EventPlumbing([],'0 = P1_LEFT\n1 = P2_LEFT\n2 = P3_LEFT\n3 = P4_LEFT\n4 = LEFT\n5 = P1_BUTTON_0\n6 = P2_BUTTON_0\n7 = P3_BUTTON_0\n8 = P4_BUTTON_0\n9 = P4_BUTTON_0')
  pb = pb.clone()
  ui.set_controller_plumbing(1, pb)

  pb.transpose_player(1)
  assert repr(pb) == '''0 = P2_LEFT
1 = P3_LEFT
2 = P4_LEFT
3 = P1_LEFT
4 = LEFT
5 = P2_BUTTON_0
6 = P3_BUTTON_0
7 = P4_BUTTON_0
8 = P1_BUTTON_0
9 = P1_BUTTON_0'''
  pb.transpose_player(2)
  assert repr(pb) == '''0 = P4_LEFT
1 = P1_LEFT
2 = P2_LEFT
3 = P3_LEFT
4 = LEFT
5 = P4_BUTTON_0
6 = P1_BUTTON_0
7 = P2_BUTTON_0
8 = P3_BUTTON_0
9 = P3_BUTTON_0'''
  pb.transpose_player(-3)
  assert repr(pb) == '''0 = P1_LEFT
1 = P2_LEFT
2 = P3_LEFT
3 = P4_LEFT
4 = LEFT
5 = P1_BUTTON_0
6 = P2_BUTTON_0
7 = P3_BUTTON_0
8 = P4_BUTTON_0
9 = P4_BUTTON_0'''

  for vl in pb.container:
    for valve in vl:
      assert valve.closed[0] == valve.open[0]
      assert valve.closed[1] == -valve.open[1]
      if valve.open[1] >= GENERIC_BUTTON:
        assert(valve.open[0] >> GENERIC_BUTTON_SHIFTER == 1)

  pb.plus(1)
  pb.plus(4)
  pb.menu_controls_enabled(False)
  pb.minus(1)
  pb.minus(4)
  pb.menu_controls_enabled(True)
  pb.plus(1)
  pb.plus(4)
  pb.minus(1)
  pb.minus(4)
  pb.menu_controls_enabled(False)
  pb.plus(4)
  pb.menu_controls_enabled(True)
  pb.minus(4)
  assert list(event_buffer) == [(1,LEFT),(-1,LEFT),(1,-LEFT),(1,LEFT),(-1,LEFT),(1,-LEFT),(-1,-LEFT),(-1,-LEFT)]
  event_buffer.clear()

  ui.set_keyboard_plumbing(default_keyboard_plumbing.clone())
  ui.set_controller_plumbing(0, EventPlumbing({},'''
  A- = P1_LEFT
  A0 = P1_UPLEFT
  A+ = P1_RIGHT
  B+ = P1_DOWN
  B0 = P1_UPRIGHT
  B- = P1_UP
  C- = P2_LEFT
  C0 = P2_UPLEFT
  C+ = P2_RIGHT
  D+ = P2_DOWN
  D0 = P2_UPRIGHT
  D- = P2_UP
  A0 B0 = P1_CENTER
  ''').clone())
  ui.set_controller_plumbing(1, default_controller_plumbing.clone())

  assert len(event_buffer) == 0
  assert len(ui.controllers[0].axis_state) == 6*1 + 3*2
  assert ui.controllers[0].axis_state == ui.controllers[1].axis_state
  assert ui.controllers[0].axis_state == [False]*12

  ui.pump([pygame.event.Event(pygame.QUIT)])
  assert len(event_buffer) == 1 and event_buffer.pop() == (-1, QUIT)
  ui.pump([pygame.event.Event(pygame.KEYDOWN, key=27)])
  assert len(event_buffer) == 1 and event_buffer.pop() == (-1, CANCEL)
  ui.pump([pygame.event.Event(pygame.KEYUP, key=27)])
  assert len(event_buffer) == 1 and event_buffer.pop() == (-1, -CANCEL)

  ui.pump([pygame.event.Event(pygame.KEYDOWN, key=305)])
  assert len(event_buffer) == 0
  ui.pump([pygame.event.Event(pygame.KEYDOWN, key=113)])
  assert len(event_buffer) == 1 and event_buffer.pop() == (-1, QUIT)
  ui.pump([pygame.event.Event(pygame.KEYUP, key=305), pygame.event.Event(pygame.KEYUP, key=113)])
  assert len(event_buffer) == 1 and event_buffer.pop() == (-1, -QUIT)
  ui.pump([pygame.event.Event(pygame.KEYDOWN, key=305)])
  assert len(event_buffer) == 0
  ui.pump([pygame.event.Event(pygame.KEYUP, key=305)])
  assert len(event_buffer) == 0
  ui.pump([pygame.event.Event(pygame.KEYDOWN, key=113)])
  assert len(event_buffer) == 0
  ui.pump([pygame.event.Event(pygame.KEYUP, key=113)])
  assert len(event_buffer) == 0

  ui.pump([pygame.event.Event(pygame.JOYHATMOTION, joy=0, hat=0, value=(-1,0)),
           pygame.event.Event(pygame.JOYHATMOTION, joy=0, hat=0, value=(-1,1)),
           pygame.event.Event(pygame.JOYHATMOTION, joy=0, hat=0, value=(0,1)),
           pygame.event.Event(pygame.JOYHATMOTION, joy=0, hat=0, value=(1,1)),
           pygame.event.Event(pygame.JOYHATMOTION, joy=0, hat=0, value=(1,0)),
           pygame.event.Event(pygame.JOYHATMOTION, joy=0, hat=0, value=(1,-1)),
           pygame.event.Event(pygame.JOYHATMOTION, joy=0, hat=0, value=(0,-1)),
           pygame.event.Event(pygame.JOYHATMOTION, joy=0, hat=0, value=(-1,-1)),
           pygame.event.Event(pygame.JOYHATMOTION, joy=0, hat=0, value=(0,0)),
  ])

  assert list(event_buffer) == [(0,LEFT),(0,UPRIGHT),(0,UP),(0,-UPRIGHT),(0,-LEFT),(0,UPLEFT),(0,-UPLEFT),(0,RIGHT),(0,-UP),(0,UPRIGHT),(0,-UPRIGHT),(0,DOWN),(0,UPLEFT),(0,-RIGHT),(0,LEFT),(0,-UPLEFT),(0,-LEFT),(0,UPLEFT),(0,UPRIGHT),(0,CENTER),(0,-DOWN)]

  event_buffer.clear()

  ui.pump([pygame.event.Event(pygame.JOYAXISMOTION, joy=0, axis=0, value=-.5),
           pygame.event.Event(pygame.JOYAXISMOTION, joy=0, axis=1, value=.5),
           pygame.event.Event(pygame.JOYAXISMOTION, joy=0, axis=0, value=0),
           pygame.event.Event(pygame.JOYAXISMOTION, joy=0, axis=0, value=.5),
           pygame.event.Event(pygame.JOYAXISMOTION, joy=0, axis=1, value=0),
           pygame.event.Event(pygame.JOYAXISMOTION, joy=0, axis=1, value=-.5),
           pygame.event.Event(pygame.JOYAXISMOTION, joy=0, axis=0, value=0),
           pygame.event.Event(pygame.JOYAXISMOTION, joy=0, axis=0, value=-.5),
           pygame.event.Event(pygame.JOYAXISMOTION, joy=0, axis=0, value=0),
           pygame.event.Event(pygame.JOYAXISMOTION, joy=0, axis=1, value=0),
  ])

  # the .5 positions are defined as within the center zone, so the above motions have never left
  # the center for either axis. The events we get are because the initial state of the controller
  # is NOT centered, but unknown, i.e. the first event for any axis will produce an event.
  assert list(event_buffer) == [(1,UPLEFT),(1,UPRIGHT)]

  event_buffer.clear()

  ui.pump([pygame.event.Event(pygame.JOYAXISMOTION, joy=0, axis=0, value=-.7),
           pygame.event.Event(pygame.JOYAXISMOTION, joy=0, axis=1, value=-.7),
           pygame.event.Event(pygame.JOYAXISMOTION, joy=0, axis=0, value=0),
           pygame.event.Event(pygame.JOYAXISMOTION, joy=0, axis=0, value=.7),
           pygame.event.Event(pygame.JOYAXISMOTION, joy=0, axis=1, value=0),
  ])

  assert list(event_buffer) == [(1,LEFT),(1,-UPLEFT),(1,UP),(1,-UPRIGHT),(1,-LEFT),(1,UPLEFT),(1,-UPLEFT),(1,RIGHT),(1,-UP),(1,UPRIGHT)]
  event_buffer.clear()

  ui.pump([pygame.event.Event(pygame.JOYAXISMOTION, joy=0, axis=1, value=.7),
           pygame.event.Event(pygame.JOYAXISMOTION, joy=0, axis=0, value=0),
           pygame.event.Event(pygame.JOYAXISMOTION, joy=0, axis=0, value=-.7),
           pygame.event.Event(pygame.JOYAXISMOTION, joy=0, axis=0, value=0),
           pygame.event.Event(pygame.JOYAXISMOTION, joy=0, axis=1, value=0),
  ])

  assert list(event_buffer) == [(1,-UPRIGHT),(1,DOWN),(1,UPLEFT),(1,-RIGHT),(1,LEFT),(1,-UPLEFT),(1,-LEFT),(1,UPLEFT),(1,UPRIGHT),(1,-DOWN)]

  event_buffer.clear()

  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=1, button=5)])
  ui.pump([pygame.event.Event(pygame.JOYBUTTONUP, joy=1, button=5)])
  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=1, button=5)])
  ui.pump([pygame.event.Event(pygame.JOYBUTTONUP, joy=1, button=5)])

  assert ui.controllers[1].generic_buttons[GENERIC_BUTTON+5] == [2,0,0,0,0]

  event_buffer.clear()

  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=1, button=2)])
  ui.pump([pygame.event.Event(pygame.JOYBUTTONUP, joy=1, button=2)])
  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=1, button=2),
           pygame.event.Event(pygame.JOYHATMOTION, joy=1, hat=0, value=(-1,0))])
  ui.pump([pygame.event.Event(pygame.JOYBUTTONUP, joy=1, button=2)])


  assert ui.poll() == (-1,LEFT)
  assert ui.poll() == (0,LEFT)
  assert ui.poll() == (-1, PASS)
  assert ui.controllers[1].generic_buttons[GENERIC_BUTTON+2] == [2,1,0,0,0]

  ui.pump([pygame.event.Event(pygame.JOYHATMOTION, joy=1, hat=0, value=(0,0))])
  event_buffer.clear()

  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=1, button=2),
           pygame.event.Event(pygame.JOYHATMOTION, joy=1, hat=0, value=(-1,0)),
           pygame.event.Event(pygame.JOYHATMOTION, joy=1, hat=0, value=(1,0)),
           pygame.event.Event(pygame.JOYHATMOTION, joy=1, hat=0, value=(0,-1)),
           pygame.event.Event(pygame.JOYHATMOTION, joy=1, hat=0, value=(0,1)),
  ])

  event_buffer.popleft() # remove the generic button event
  assert list(event_buffer) == [(-1,LEFT),(0,LEFT),(-1,-LEFT),(0,-LEFT),(-1,RIGHT),(0,RIGHT),(-1,-RIGHT),(0,-RIGHT),(-1,DOWN),(0,DOWN),(-1,UP),(0,UP),(-1,-DOWN),(0,-DOWN)]
  assert ui.controllers[1].generic_buttons[GENERIC_BUTTON+2] == [3,2,1,1,1]

  blueprint = '''
  A- = P1_LEFT
  A+ = P1_RIGHT
  B+ = P1_DOWN
  B- = P1_UP
  0 = P1_BUTTON_0
  1 = P1_BUTTON_1
  2 = P1_BUTTON_2
  3 = P1_BUTTON_3
  4 = P1_BUTTON_4
  5 = P1_BUTTON_5
  '''

  event_buffer.clear()
  joymock = type('',(object,),{
    'get_name':lambda self: 'Mock Stick',
    'get_numhats': lambda self: 1,
    'get_numaxes': lambda self: 0,
  })()
  joymock2 = type('',(object,),{
    'get_name':lambda self: 'Mock Stick',
    'get_numhats': lambda self: 1,
    'get_numaxes': lambda self: 0,
  })()
  ui.controllers = [Controller(joymock), Controller(joymock2)]
  ui.oddeven_learned = True
  ui.oddeven = 1

  ui.set_controller_plumbing(0, EventPlumbing([], blueprint).clone())
  ui.set_controller_plumbing(1, EventPlumbing({}, blueprint).clone())

  # prime all buttons with 2 "wrong" inputs (i.e. no axis activated simultaneously)
  for joy in range(2):
    ui.pump([pygame.event.Event(pygame.JOYHATMOTION, joy=joy, hat=0, value=(0,0))])
    for n in range(2):
      for butt in range(6):
        ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=joy, button=butt)])
        ui.pump([pygame.event.Event(pygame.JOYBUTTONUP, joy=joy, button=butt)])

  event_buffer.clear()
  directions = [[(-1,0),(1,0),(0,-1),(0,1)],[(1,0),(0,-1),(0,1),(-1,0)]]

  for i in range(4*MIN_LEARN_GENERIC_BUTTON_COUNT): # 4*... should be enough to guarantee a result
    if len(ui.controllers[0].generic_buttons)+len(ui.controllers[1].generic_buttons) == 0: break

    for butt in range(6):
      for joy in range(2):
        if butt >= 4:
          ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=joy, button=butt)])
          ui.pump([pygame.event.Event(pygame.JOYBUTTONUP, joy=joy, button=butt)])
        else:
          ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=joy, button=butt),
                   pygame.event.Event(pygame.JOYHATMOTION, joy=joy, hat=0, value=directions[joy][butt])])
          ui.pump([pygame.event.Event(pygame.JOYBUTTONUP, joy=joy, button=butt),
                   pygame.event.Event(pygame.JOYHATMOTION, joy=joy, hat=0, value=(0,0))])

  else:
    raise AssertionError("Learning did not work")

  event_buffer.clear()


  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=0, button=0),
           pygame.event.Event(pygame.JOYHATMOTION, joy=0, hat=0, value=(-1,0))])
  assert ui.poll() == (0,LEFT) and len(event_buffer) == 0
  ui.pump([pygame.event.Event(pygame.JOYBUTTONUP, joy=0, button=0)])
  assert len(event_buffer) == 0
  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=0, button=0)])
  assert len(event_buffer) == 0
  ui.pump([pygame.event.Event(pygame.JOYHATMOTION, joy=0, hat=0, value=(0,0))])
  assert len(event_buffer) == 0
  ui.pump([pygame.event.Event(pygame.JOYBUTTONUP, joy=0, button=0)])
  assert ui.poll() == (0,-LEFT) and len(event_buffer) == 0
  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=1, button=0),
           pygame.event.Event(pygame.JOYBUTTONUP, joy=1, button=0)])
  assert [ui.poll(),ui.poll()] == [(0,RIGHT),(0,-RIGHT)]
  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=0, button=1),
           pygame.event.Event(pygame.JOYBUTTONUP, joy=0, button=1)])
  assert [ui.poll(),ui.poll()] == [(0,RIGHT),(0,-RIGHT)]
  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=1, button=1),
           pygame.event.Event(pygame.JOYBUTTONUP, joy=1, button=1)])
  assert [ui.poll(),ui.poll()] == [(0,DOWN),(0,-DOWN)]
  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=0, button=2),
           pygame.event.Event(pygame.JOYBUTTONUP, joy=0, button=2)])
  assert [ui.poll(),ui.poll()] == [(0,DOWN),(0,-DOWN)]
  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=1, button=2),
           pygame.event.Event(pygame.JOYBUTTONUP, joy=1, button=2)])
  assert [ui.poll(),ui.poll()] == [(0,UP),(0,-UP)]
  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=0, button=3),
           pygame.event.Event(pygame.JOYBUTTONUP, joy=0, button=3)])
  assert [ui.poll(),ui.poll()] == [(0,UP),(0,-UP)]
  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=1, button=3),
           pygame.event.Event(pygame.JOYBUTTONUP, joy=1, button=3)])
  assert [ui.poll(),ui.poll()] == [(0,LEFT),(0,-LEFT)]
  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=0, button=4),
           pygame.event.Event(pygame.JOYBUTTONUP, joy=0, button=4)])
  assert [ui.poll(),ui.poll()] == [(-1,PASS),(-1,PASS)]
  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=1, button=4),
           pygame.event.Event(pygame.JOYBUTTONUP, joy=1, button=4)])
  assert [ui.poll(),ui.poll()] == [(-1,PASS),(-1,PASS)]
  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=0, button=5),
           pygame.event.Event(pygame.JOYBUTTONUP, joy=0, button=5)])
  assert [ui.poll(),ui.poll()] == [(-1,PASS),(-1,PASS)]
  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=1, button=5),
           pygame.event.Event(pygame.JOYBUTTONUP, joy=1, button=5)])
  assert [ui.poll(),ui.poll()] == [(-1,PASS),(-1,PASS)]

  assert len(event_buffer) == 0

  assert repr(ui.controllers[0].plumbing) == '''
0 = P1_LEFT
1 = P1_RIGHT
2 = P1_DOWN
3 = P1_UP
A- = P1_LEFT
A+ = P1_RIGHT
B- = P1_UP
B+ = P1_DOWN
  '''.strip()

  assert repr(ui.controllers[1].plumbing) == '''
0 = P1_RIGHT
1 = P1_DOWN
2 = P1_UP
3 = P1_LEFT
A- = P1_LEFT
A+ = P1_RIGHT
B- = P1_UP
B+ = P1_DOWN
  '''.strip()

  ui.repeat_output()
  assert len(event_buffer) == 0

  oldREPEATABLE = REPEATABLE
  REPEATABLE = frozenset([(0,LEFT)])

  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=0, button=0)])
  assert ui.poll() == (0, LEFT) and len(event_buffer) == 0
  assert ui.count_open_valves() == 1
  ui.repeat_output()
  assert ui.poll() == (0, LEFT) and len(event_buffer) == 0

  REPEATABLE = oldREPEATABLE

  ui.set_controller_plumbing(0, EventPlumbing([],'''
  A- 0 = P1_BUTTON_0
  A- = P1_LEFT
  ''').clone())

  for i in range(4*MIN_LEARN_GENERIC_BUTTON_COUNT): # 4*... should be enough to guarantee a result
    ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=0, button=0),
    pygame.event.Event(pygame.JOYHATMOTION, joy=0, hat=0, value=(-1,0))])
    ui.pump([pygame.event.Event(pygame.JOYBUTTONUP, joy=0, button=0),
    pygame.event.Event(pygame.JOYHATMOTION, joy=0, hat=0, value=(0,0))])

  event_buffer.clear()

  ui.pump([pygame.event.Event(pygame.JOYHATMOTION, joy=0, hat=0, value=(-1,0))])
  assert ui.poll() == (0, LEFT)
  ui.pump([pygame.event.Event(pygame.JOYHATMOTION, joy=0, hat=0, value=(0,0))])
  assert ui.poll() == (0, -LEFT)

  ui.set_controller_plumbing(0, EventPlumbing([],'''
  A- 0 = P1_BUTTON_0
  A- = P1_LEFT
  2 = P1_LEFT
  2 = P1_BUTTON_0
  3 4 = P1_BUTTON_0
  5 = P1_BUTTON_1 P1_BUTTON_3
  7 = P1_BUTTON_3 P1_BUTTON_1
  8 9 = P1_BUTTON_0 P1_LEFT
  ''').clone())

  for i in range(4*MIN_LEARN_GENERIC_BUTTON_COUNT): # 4*... should be enough to guarantee a result
    ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=0, button=0),
    pygame.event.Event(pygame.JOYHATMOTION, joy=0, hat=0, value=(-1,0))])
    ui.pump([pygame.event.Event(pygame.JOYBUTTONUP, joy=0, button=0),
    pygame.event.Event(pygame.JOYHATMOTION, joy=0, hat=0, value=(0,0))])
    ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=0, button=5)])
    ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=0, button=7)])
    ui.pump([pygame.event.Event(pygame.JOYBUTTONUP, joy=0, button=5)])
    ui.pump([pygame.event.Event(pygame.JOYBUTTONUP, joy=0, button=7)])

  event_buffer.clear()

  assert repr(ui.controllers[0].plumbing).strip() == '''
2 = P1_LEFT
3 4 = P1_LEFT
8 9 = P1_LEFT
0 A- = P1_LEFT
A- = P1_LEFT
'''.strip()
  assert ui.count_valves(0) == 1+1 # the +1 is for the -(1,PASS) valve that is not output


  ui.pump([pygame.event.Event(pygame.JOYHATMOTION, joy=0, hat=0, value=(-1,0))])
  assert ui.poll() == (0, LEFT)
  ui.pump([pygame.event.Event(pygame.JOYHATMOTION, joy=0, hat=0, value=(0,0))])
  assert ui.poll() == (0, -LEFT)

  pb = EventPlumbing([],'0 = P1_LEFT')
  pb.header = "[Biggus Stickus]"
  pb.filename = "50-biggus_2.cfg"
  
  plumbing_templates = {"Biggus Stickus":[None,None,pb]}
  
  pb = get_plumbing("keyboard",0)
  assert pb.filename == "10-keyboard.cfg"
  assert pb.header == "[keyboard]"
  assert pb.is_keyboard
  assert pb.menuing_disabled == False

  pb = get_plumbing("Big Stick",0)
  assert pb.filename == "10-bigstick.cfg"
  assert pb.header == "[Big Stick]"
  assert not pb.is_keyboard
  assert pb.menuing_disabled == False
  assert "P1_" in repr(pb) and not "P2_" in repr(pb)

  pb = get_plumbing("Big Stick",1)
  assert pb.filename == "10-bigstick_1.cfg"
  assert pb.header == "[Big Stick #1]"
  assert not pb.is_keyboard
  assert pb.menuing_disabled == True
  assert "P2_" in repr(pb) and not "P1_" in repr(pb)

  pb = get_plumbing("Biggus Stickus",2)
  assert pb.filename == "50-biggus_2.cfg"
  assert pb.header == "[Biggus Stickus]"
  assert not pb.is_keyboard
  assert pb.menuing_disabled == False
  assert "P1_" in repr(pb) and not "P2_" in repr(pb)
  
  pb = get_plumbing("Biggus Stickus",3)
  assert pb.filename == "10-biggus_3.cfg"
  assert pb.header == "[Biggus Stickus #3]"
  assert not pb.is_keyboard
  assert pb.menuing_disabled == True
  assert "P2_" in repr(pb) and not "P1_" in repr(pb)
  
  pb = get_plumbing("Biggus Stickus",1)
  assert pb.filename == "10-biggus_1.cfg"
  assert pb.header == "[Biggus Stickus #1]"
  assert not pb.is_keyboard
  assert pb.menuing_disabled == True
  assert "P4_" in repr(pb) and not "P1_" in repr(pb)

#  ui.controller_plumbing[0].visit(DebugValves())

  read_plumbing_templates()
  ui.force_init_controllers()
  ui.clear()
  

  while True:
    pid, evid = ui.poll()
    if evid == PASS:
      pygame.time.wait(5)
    elif evid == QUIT:
      raise SystemExit
    elif evid == SCREENSHOT:
      print("[Events history]")
      for lst in ui.pygame_events:
        for ev in lst:
          print(ev)
      for joy in range(len(ui.controllers)):
        print("[Controller %d]" % joy)
        print(repr(ui.controllers[joy].plumbing))
        ui.controllers[joy].plumbing.visit(DebugValves())
        print("Total number of valve objects: %d" % ui.count_valves(joy))
    else:
      dir = evid
      pressed=(255,0,0)
      if dir < 0:
        dir = -dir
        pressed=(0,0,0)
      if pid >= 0 and dir == UP: screen.fill(pressed, rect=pygame.Rect(100,0,100,100))
      if pid >= 0 and dir == DOWN: screen.fill(pressed, rect=pygame.Rect(100,200,100,100))
      if pid >= 0 and dir == LEFT: screen.fill(pressed, rect=pygame.Rect(0,100,100,100))
      if pid >= 0 and dir == RIGHT: screen.fill(pressed, rect=pygame.Rect(200,100,100,100))
      if pid < 0 and dir == CONFIRM: screen.fill(pressed, rect=pygame.Rect(100,100,100,100))
      pygame.display.flip()
      print(evstr(pid,evid))

####################################################################################################
#
#     ANY NON-TESTING CODE SHOULD BE ADDED ABOVE THE PRECEDING MAIN() CODE BLOCK
#
####################################################################################################
