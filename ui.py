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

MIN_LEARN_GENERIC_BUTTON_COUNT = 5
LEARN_GENERIC_BUTTON_DIRECTION = .85
LEARN_GENERIC_BUTTON_INDEPENDENT = .5

# If no output change occurs for this many milliseconds, UI.poll() will start
# repeating REPEATABLE outputs that are being held. Mostly used so that you
# can hold an arrow to move through menus and don't have to constantly tap it.
REPEAT_INITIAL_DELAY = 250

# Delay between repeats after REPEAT_INITIAL_DELAY.
REPEAT_DELAY = 33

# Events that are repeatable by auto-repeat (see REPEAT_INITIAL_DELAY and poll())
REPEATABLE = frozenset(((-1,UP),(-1,DOWN),(-1,LEFT),(-1,RIGHT)))

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

  def plus(self):
    if self.pressure == 0:
      self.evlist.append(self.open)
    self.pressure += 1

  def minus(self):
    self.pressure -= 1
    if self.pressure == 0:
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
      output_strings.append(valvenames[(pid,evid)])
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

    self.is_keyboard = False
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

  def clone(self):
    '''
    Creates an independent copy of this valve network. In particular it makes copies of the
    output valves that have been taken directly from ui.valves by the constructor.
    It is necessary to clone() an EventPlumbing before using its plus()/minus().
    '''
    return deepcopy(self)

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
6 = P1_BUTTON_6
7 = P1_BUTTON_7
8 = P1_BUTTON_8
9 = P1_BUTTON_9
10 = P1_BUTTON_10
11 = P1_BUTTON_11
12 = P1_BUTTON_12
13 = P1_BUTTON_13
14 = P1_BUTTON_14
15 = P1_BUTTON_15
16 = P1_BUTTON_16
17 = P1_BUTTON_17
18 = P1_BUTTON_18
19 = P1_BUTTON_19
20 = P1_BUTTON_20
21 = P1_BUTTON_21
22 = P1_BUTTON_22
23 = P1_BUTTON_23
24 = P1_BUTTON_24
25 = P1_BUTTON_25
26 = P1_BUTTON_26
27 = P1_BUTTON_27
28 = P1_BUTTON_28
29 = P1_BUTTON_29
30 = P1_BUTTON_30
31 = P1_BUTTON_31
''')

class GenericButtonsFixup(object):
  '''
  Adds a number to the pid of every EventValve's output event that refers to a
  event id >= GENERIC_BUTTON.
  '''
  def __init__(self, fixup):
    self.fixup = fixup

  def visit(self, inputs, outputs):
    for valve in outputs:
      if valve.open[1] >= GENERIC_BUTTON and valve.open[0] < MAX_PLAYERS: # the 2nd check is to prevent fixing up twice
        valve.open = (valve.open[0]+self.fixup, valve.open[1])
        valve.closed = (valve.closed[0]+self.fixup, valve.closed[1])

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


class UI(object):
  '''
  Receives raw user input from pygame.event and translates
  it into higher level semantic events used by pydance. The translation is highly
  configurable and permits things such as translating 2 simultaneous button presses
  into a ui.SCREENSHOT event. See EventPlumbing.
  '''
  def __init__(self, ebuf = event_buffer):


    # stores tuples (pid, evid) or (pid, -evid) for button presses/releases.
    # Used as FIFO. EventValves append data. poll() and poll_dance() remove it.
    self.event_buffer = ebuf

    # this logs the last time an output even was produced in pump()
    self.last_valve_change_time = pygame.time.get_ticks()

    # this is the earliest time at which poll() will trigger an auto-repeat.
    self.next_repeat_time = pygame.time.get_ticks()

    # the EventPlumbing used for keyboard inputs.
    self.keyboard_plumbing = None

    # list of pygame.joystick.Joystick objects. Array index matches pygame's joystick id.
    self.controllers = []

    # maps a pygame.joystick.Joystick.get_name() to a list of indexes into self.controllers that share that name.
    self.name2controllers = {}

    # controller_plumbing[i] is the EventPlumbing used for controllers[i].
    self.controller_plumbing = []

    # num_hats[i] is the number of hats of controllers[i]
    self.num_hats = []

    # axis_state[i] is a list of N booleans corresponding to the virtual buttons derived from
    # the axes of controllers[i]. The list always starts with states derived from hats. Each
    # hat generates 6 axis states: x < 0, x == 0, x > 0, y < 0, y == 0, y > 0
    # This is followed by the axis states for the analog axes. Each analog axis generates
    # 3 axis states: a < -.5, a in [-.5,.5], a > .5
    self.axis_state = []

    # Used for learning the relationship (if any) of generic buttons with directional axes.
    # for button >= GENERIC_BUTTON: generic_buttons[(pid,button)] = [total, left, right, up, down]
    # where 0 <= pid, pid is the player number ANDed with controller index << GENERIC_BUTTON_SHIFTER.
    # total is the total number of times this button has been seen in the event_buffer
    #       up is the number of times (pid, UP) was seen at the same time in the event_buffer
    #       ditto for left,right,down
    self.generic_buttons = {}

    self.oddeven = 1 # 1 => odd, 0 => even

    # We learn the oddeven setting from the first press of a generic button with no
    # simultaneous directional input. The idea is that the first button someone presses in
    # the game is going to be CONFIRM to activate one of the menu items.
    self.oddeven_learned = False

    self.learn_sound = pygame.mixer.Sound(os.path.join(sound_path, "assist-l.ogg"))
    self.learn_sound.set_volume(.345)

    self.init_controllers()

  def init_controllers(self):
    pygame.joystick.quit() # turn of joystick module to make sure following init() rescans for controllers
    pygame.joystick.init()
    self.controllers = []
    for joy in range(pygame.joystick.get_count()):
      self.controllers.append(pygame.joystick.Joystick(joy))
      self.controllers[joy].init()

    self.init_controllers_continued()

  def init_controllers_continued(self):
    '''
    This function assumes that self.controllers is filled with pygame.joystick.Joystick objects
    that are active and ready to send events (i.e. init() has been called on them). Everything
    else will be re-initialized. The init_controllers()/init_controllers_continued() split exists
    to allow implementing unit tests using mock joystick objects.
    '''
    self.oddeven = 1 # Most gamepads seem to have Start on an odd-numbered button (based on https://raw.githubusercontent.com/gabomdq/SDL_GameControllerDB/master/gamecontrollerdb.txt).
    self.oddeven_learned = False
    self.keyboard_plumbing = default_keyboard_plumbing.clone()
    self.name2controllers = {}
    self.controller_plumbing = []
    self.num_hats = []
    self.axis_state = []
    for joy in range(len(self.controllers)):
      self.name2controllers.setdefault(self.controllers[joy].get_name(),[]).append(joy)
      self.controller_plumbing.append(default_controller_plumbing.clone())
      self.controller_plumbing[joy].visit(GenericButtonsFixup(joy << GENERIC_BUTTON_SHIFTER))
      hats = self.controllers[joy].get_numhats()
      self.num_hats.append(hats)
      axes = self.controllers[joy].get_numaxes()
      self.axis_state.append([])
      for h in range(hats):
        self.axis_state[joy].extend((False,False,False,False,False,False))
      for a in range(axes):
        self.axis_state[joy].extend((False,False,False))

  def set_keyboard_plumbing(self, kb):
    self.keyboard_plumbing = kb

  def set_controller_plumbing(self, joy, pb):
    self.generic_buttons = {}
    self.controller_plumbing[joy] = pb
    self.controller_plumbing[joy].visit(GenericButtonsFixup(joy << GENERIC_BUTTON_SHIFTER))

  def poll(self):
    '''
    Returns a pair (pid, evid) or (pid, -evid) where
      * pid is the player id the event belongs to (0..MAX_PLAYERS-1) or -1 if it is
        a non-player specific event (menuing).
        NOTE: Directions like UP may create 2 events, one with pid < 0 and one with a
        pid >= 0. Make sure you always evaluate pid when checking for directions.
      * evid is the event id (e.g. CONFIRM). It is positive when the event is asserted
        and negative when the event is de-asserted.

    The special evid 0 (==PASS) is returned with pid<0 when no event is pending.

    This function also converts all buttons >= GENERIC_BUTTON to either CONFIRM or CANCEL.

    If no events occur for REPEAT_INITIAL_DELAY, outputs that are being held and are in the
    REPEATABLE set will be repeated every REPEAT_DELAY ms.

    During dancing, the special function poll_dance() is used instead of this one.
    '''
    if len(self.event_buffer) == 0:
      self.pump(pygame.event.get())
      self._translate_generic_buttons()

    if len(self.event_buffer) == 0:
      if pygame.time.get_ticks() > self.last_valve_change_time + REPEAT_INITIAL_DELAY:
        if pygame.time.get_ticks() > self.next_repeat_time:
          self.next_repeat_time = pygame.time.get_ticks() + REPEAT_DELAY
          self.repeat_output()

      if len(self.event_buffer) == 0:
        return (-1, PASS)

    return self.event_buffer.popleft()

  def repeat_output(self):
    '''
    All currently open valves will put a copy of their open event into the event queue.
    Note that this will cause a discrepancy between press and release events, so this
    function must not be used in a context that tracks state (such as the dance loop,
    which is why it uses poll_dance() which does not use auto-repeating).
    '''
    self.keyboard_plumbing.visit(RepeatEvent())
    for joy in range(len(self.controllers)):
      self.controller_plumbing[joy].visit(RepeatEvent())

  def count_open_valves(self):
    ''' Returns the number of currently open output valves, i.e. asserted events.'''
    count = CountOpenValves()
    for joy in range(len(self.controllers)):
      self.controller_plumbing[joy].visit(count)
    return count.count

  def count_valves(self, joy):
    ''' Returns the total number of output valves for controller joy.'''
    count = CountOpenValves()
    self.controller_plumbing[joy].visit(count)
    return len(count.already_done)

  def poll_dance(self):
    '''
    Similar to poll() but filters out some events you don't want during the dance part.
    In particular it does not return CONFIRM/CANCEL for GENERIC_BUTTONs.
    Also does not have any auto-repeat functionality.

    The special evid 0 (==PASS) is returned with pid<0 when no event is pending.
    '''
    if len(self.event_buffer) == 0:
      self.pump(pygame.event.get())

    while len(self.event_buffer) > 0 and (
          self.event_buffer[0][1] >= GENERIC_BUTTON or self.event_buffer[0][1] <= -GENERIC_BUTTON):
      self.event_buffer.popleft()

    if len(self.event_buffer) == 0:
      return (-1, PASS)

    return self.event_buffer.popleft()

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
    num_events = len(self.event_buffer)
    button_presses = 0
    for event in events:
      if event.type == pygame.QUIT:
        self.event_buffer.append((-1,QUIT))
      elif event.type == pygame.KEYDOWN:
        self.keyboard_plumbing.plus(event.key)
      elif event.type == pygame.KEYUP:
        self.keyboard_plumbing.minus(event.key)
      elif event.type == pygame.JOYBUTTONDOWN:
        if event.button < MAX_BUTTONS:  # we use numbers >= MAX_BUTTONS for axes
          button_presses += 1
          self.controller_plumbing[event.joy].plus(event.button)
      elif event.type == pygame.JOYBUTTONUP:
        if event.button < MAX_BUTTONS:  # we use numbers >= MAX_BUTTONS for axes
          self.controller_plumbing[event.joy].minus(event.button)
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
        axis = MAX_BUTTONS + self.num_hats[event.joy] * 6 + event.axis * 3
        a = event.value
        self._handle_axis(event.joy, axis, a < -.5)
        self._handle_axis(event.joy, axis+1, a >= -.5 and a <= .5)
        self._handle_axis(event.joy, axis+2, a > .5)

    if len(self.event_buffer) > num_events:
      self.last_valve_change_time = pygame.time.get_ticks()

    # We only trigger the learning code if there is exactly 1 button press in the events
    # This avoids screwing up the learning in cases like <- -> where only one axis direction
    # can be reported but both buttons.
    # It would be better to do the whole learning (and skipping it on multi-button presses)
    # separately for each controller, but that seems not worth the effort, as I expect it
    # to be a very rare occurence for 2 people to play a multi-player game with 2 unknown
    # controllers at the same time. And even if, all it does is prolong the learning process.
    if button_presses == 1:
      for i in range(num_events, len(self.event_buffer)):
        pid, evid = self.event_buffer[i]
        if evid >= GENERIC_BUTTON or evid <= -GENERIC_BUTTON:
          self._handle_generic_buttons(num_events)
          break

  def _handle_axis(self, joy, axis, state):
    '''
    Update state (boolean) of axis for controller joy and pump into plumbing if it changed.
    '''
    if self.axis_state[joy][axis-MAX_BUTTONS] != state:
      self.axis_state[joy][axis-MAX_BUTTONS] = state
      if state:
        self.controller_plumbing[joy].plus(axis)
      else:
        self.controller_plumbing[joy].minus(axis)

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
          generic.append((pid,evid))

    if not self.oddeven_learned and len(active) == 0 and len(generic) > 0:
      self.oddeven_learned = True
      self.oddeven = generic[0][1] & 1

    for (pid,evid) in generic:
      gen = self.generic_buttons.setdefault((pid,evid),[0,0,0,0,0])
      gen[0] += 1
      gen[1] += active.get((pid & PLAYERS_MASK,LEFT),0)
      gen[2] += active.get((pid & PLAYERS_MASK,RIGHT),0)
      gen[3] += active.get((pid & PLAYERS_MASK,UP),0)
      gen[4] += active.get((pid & PLAYERS_MASK,DOWN),0)

      if gen[0] > MIN_LEARN_GENERIC_BUTTON_COUNT:
        indep = 0
        count = float(gen[0])
        for i in (1,2,3,4):
          p = gen[i]/count
          if p > LEARN_GENERIC_BUTTON_DIRECTION:
            joy = pid >> GENERIC_BUTTON_SHIFTER
            self._learn_generic_button(joy, (pid,evid), pid & PLAYERS_MASK, (LEFT,RIGHT,UP,DOWN)[i-1])
            break
          elif p < LEARN_GENERIC_BUTTON_INDEPENDENT:
            indep += 1

        if indep == 4:
          joy = pid >> GENERIC_BUTTON_SHIFTER
          if evid & 1 == self.oddeven:
            self._learn_generic_button(joy, (pid,evid), -1, CONFIRM)
          else:
            self._learn_generic_button(joy, (pid,evid), -1, CANCEL)

  def _learn_generic_button(self, joy, oldpidevid, pid, evid):
    self.learn_sound.play()
    found = FindEvent(pid,evid)
    self.controller_plumbing[joy].visit(found)
    if found.valve is not None:
      self.controller_plumbing[joy].replace_valve(oldpidevid,found.valve)
    else:
      self.controller_plumbing[joy].visit(ReplaceEvent(oldpidevid, pid, evid))
    del self.generic_buttons[oldpidevid]

  def _translate_generic_buttons(self):
    for pid, evid in self.event_buffer:
      # if we find any non-generic button events, discard all generic button events
      # This should protect us from any weird shadow buttons.
      if pid < 0 or (evid < GENERIC_BUTTON and evid > -GENERIC_BUTTON):
        i = 0
        while i < len(self.event_buffer):
          pid, evid = self.event_buffer[i]
          if evid <= -GENERIC_BUTTON or evid >= GENERIC_BUTTON:
            del self.event_buffer[i]
            i -= 1
          i += 1
        break
    else:
      for i in range(len(self.event_buffer)):
        pid, evid = self.event_buffer[i]
        if pid >= 0:
          if evid <= -GENERIC_BUTTON:
            if (-evid) & 1 == self.oddeven:
              self.event_buffer[i] = (-1, -CONFIRM)
            else:
              self.event_buffer[i] = (-1, -CANCEL)
          elif evid >= GENERIC_BUTTON:
            if evid & 1 == self.oddeven:
              self.event_buffer[i] = (-1, CONFIRM)
            else:
              self.event_buffer[i] = (-1, CANCEL)



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
  valve.minus()
  assert len(event_buffer) == 0
  valve.plus()
  assert len(event_buffer) == 0
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
  valve.plus()
  assert len(event_buffer) == 1 and event_buffer[0] == (-2, 42)
  valve.minus()
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
  fork3 = EventForkValve(-2, [v1,v2,v4])
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
  fork2.plus()
  assert len(event_buffer) == 0
  fork2.minus()
  fork3.plus()
  assert len(event_buffer) == 0
  fork3.plus()
  assert len(event_buffer) == 1 and event_buffer.pop() == (666, 1)
  fork1.minus()
  assert len(event_buffer) == 1 and event_buffer.pop() == (666, -1)
  fork2.minus()
  assert len(event_buffer) == 1 and event_buffer.pop() == (42, -2)
  fork3.minus()
  assert len(event_buffer) == 1 and event_buffer.pop() == (69, -3)
  assert len(event_buffer) == 0
  fork1.plus()
  fork2.plus()
  fork2.plus()
  fork3.plus()
  fork3.plus()
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
  fork2.plus()
  assert len(event_buffer) == 0
  fork2.minus()
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
  ui.controllers = [joymock, joymock2]
  ui.init_controllers_continued()

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

  assert ui.name2controllers['Mock Stick'] == [0,1]
  assert len(event_buffer) == 0
  assert len(ui.axis_state) == 2 and len(ui.axis_state[0]) == 6*1 + 3*2
  assert ui.axis_state[0] == ui.axis_state[1]
  assert ui.axis_state[0] == [False]*12

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

  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=1, button=9),
           pygame.event.Event(pygame.JOYBUTTONUP, joy=1, button=9)])
  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=1, button=9),
           pygame.event.Event(pygame.JOYBUTTONUP, joy=1, button=9),
  ])

  assert ui.generic_buttons[(0+1<<GENERIC_BUTTON_SHIFTER, GENERIC_BUTTON+9)] == [2,0,0,0,0]
  ui._translate_generic_buttons()
  assert list(event_buffer) == [(-1,CONFIRM),(-1,-CONFIRM),(-1,CONFIRM),(-1,-CONFIRM)]

  event_buffer.clear()
  event_buffer.append((0,GENERIC_BUTTON+2))

  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=1, button=2),
           pygame.event.Event(pygame.JOYBUTTONUP, joy=1, button=2)])
  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=1, button=2),
           pygame.event.Event(pygame.JOYBUTTONUP, joy=1, button=2),
           pygame.event.Event(pygame.JOYHATMOTION, joy=1, hat=0, value=(-1,0)),
  ])

  ui._translate_generic_buttons()
  assert list(event_buffer) == [(-1,LEFT),(0,LEFT)]
  assert ui.generic_buttons[(0+1<<GENERIC_BUTTON_SHIFTER, GENERIC_BUTTON+2)] == [2,1,0,0,0]


  event_buffer.clear()
  event_buffer.append((0,GENERIC_BUTTON+2))

  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=1, button=2),
           pygame.event.Event(pygame.JOYBUTTONUP, joy=1, button=2)])
  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=1, button=2),
           pygame.event.Event(pygame.JOYBUTTONUP, joy=1, button=2),
  ])
  ui._translate_generic_buttons()
  assert list(event_buffer) == [(-1, CANCEL),(-1,CANCEL),(-1,-CANCEL),(-1,CANCEL),(-1,-CANCEL)]



  ui.pump([pygame.event.Event(pygame.JOYHATMOTION, joy=1, hat=0, value=(0,0))])
  event_buffer.clear()

  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=1, button=2),
           pygame.event.Event(pygame.JOYHATMOTION, joy=1, hat=0, value=(-1,0)),
           pygame.event.Event(pygame.JOYHATMOTION, joy=1, hat=0, value=(1,0)),
           pygame.event.Event(pygame.JOYHATMOTION, joy=1, hat=0, value=(0,-1)),
           pygame.event.Event(pygame.JOYHATMOTION, joy=1, hat=0, value=(0,1)),
  ])

  ui._translate_generic_buttons()
  assert list(event_buffer) == [(-1,LEFT),(0,LEFT),(-1,-LEFT),(0,-LEFT),(-1,RIGHT),(0,RIGHT),(-1,-RIGHT),(0,-RIGHT),(-1,DOWN),(0,DOWN),(-1,UP),(0,UP),(-1,-DOWN),(0,-DOWN)]
  assert ui.generic_buttons[(0+1<<GENERIC_BUTTON_SHIFTER, GENERIC_BUTTON+2)] == [5,2,1,1,1]

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
  ui.controllers = [joymock, joymock2]
  ui.init_controllers_continued()
  ui.oddeven_learned = True
  ui.oddeven = 1

  ui.set_controller_plumbing(0, EventPlumbing([], blueprint).clone())
  ui.set_controller_plumbing(1, EventPlumbing({}, blueprint).clone())

  # prime all buttons with 2 "wrong" inputs (i.e. no axis activated simultaneously)
  for joy in range(2):
    ui.pump([pygame.event.Event(pygame.JOYHATMOTION, joy=joy, hat=0, value=(0,0))])
    for n in range(2):
      for butt in range(6):
        ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=joy, button=butt),
                 pygame.event.Event(pygame.JOYBUTTONUP, joy=joy, button=butt)])

  event_buffer.clear()
  directions = [[(-1,0),(1,0),(0,-1),(0,1)],[(1,0),(0,-1),(0,1),(-1,0)]]

  for i in range(4*MIN_LEARN_GENERIC_BUTTON_COUNT): # 4*... should be enough to guarantee a result
    if len(ui.generic_buttons) == 0: break

    for butt in range(6):
      for joy in range(2):
        if butt >= 4:
          if joy == 0:
            ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=joy, button=butt),
                   pygame.event.Event(pygame.JOYBUTTONUP, joy=joy, button=butt)])
          else:
            ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=joy, button=butt)])
            ui.pump([pygame.event.Event(pygame.JOYBUTTONUP, joy=joy, button=butt)])
        else:
          if joy == 0:
            ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=joy, button=butt),
                   pygame.event.Event(pygame.JOYBUTTONUP, joy=joy, button=butt),
                   pygame.event.Event(pygame.JOYHATMOTION, joy=joy, hat=0, value=directions[joy][butt]),
                   pygame.event.Event(pygame.JOYHATMOTION, joy=joy, hat=0, value=(0,0)),
                   ])
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
  assert [ui.poll(),ui.poll()] == [(-1,CANCEL),(-1,-CANCEL)]
  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=1, button=4),
           pygame.event.Event(pygame.JOYBUTTONUP, joy=1, button=4)])
  assert [ui.poll(),ui.poll()] == [(-1,CANCEL),(-1,-CANCEL)]
  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=0, button=5),
           pygame.event.Event(pygame.JOYBUTTONUP, joy=0, button=5)])
  assert [ui.poll(),ui.poll()] == [(-1,CONFIRM),(-1,-CONFIRM)]
  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=1, button=5),
           pygame.event.Event(pygame.JOYBUTTONUP, joy=1, button=5)])
  assert [ui.poll(),ui.poll()] == [(-1,CONFIRM),(-1,-CONFIRM)]

  assert len(event_buffer) == 0

  assert repr(ui.controller_plumbing[0]) == '''
0 = P1_LEFT
1 = P1_RIGHT
2 = P1_DOWN
3 = P1_UP
4 = CANCEL
5 = CONFIRM
A- = P1_LEFT
A+ = P1_RIGHT
B- = P1_UP
B+ = P1_DOWN
  '''.strip()

  assert repr(ui.controller_plumbing[1]) == '''
0 = P1_RIGHT
1 = P1_DOWN
2 = P1_UP
3 = P1_LEFT
4 = CANCEL
5 = CONFIRM
A- = P1_LEFT
A+ = P1_RIGHT
B- = P1_UP
B+ = P1_DOWN
  '''.strip()

  ui.repeat_output()
  assert len(event_buffer) == 0

  ui.pump([pygame.event.Event(pygame.JOYBUTTONDOWN, joy=0, button=5),
           pygame.event.Event(pygame.JOYBUTTONDOWN, joy=1, button=5)])

  assert [ui.poll(),ui.poll()] == [(-1,CONFIRM),(-1,CONFIRM)]
  assert len(event_buffer) == 0
  ui.repeat_output()
  assert len(event_buffer) == 0 # CONFIRM is not repeatable

  ui.pump([pygame.event.Event(pygame.JOYBUTTONUP, joy=0, button=5),
           pygame.event.Event(pygame.JOYBUTTONUP, joy=1, button=5)])

  assert [ui.poll(),ui.poll()] == [(-1,-CONFIRM),(-1,-CONFIRM)]
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

  assert repr(ui.controller_plumbing[0]).strip() == '''
2 = P1_LEFT
3 4 = P1_LEFT
5 = CONFIRM
7 = CONFIRM
8 9 = P1_LEFT
0 A- = P1_LEFT
A- = P1_LEFT
'''.strip()
  assert ui.count_valves(0) == 2


  ui.pump([pygame.event.Event(pygame.JOYHATMOTION, joy=0, hat=0, value=(-1,0))])
  assert ui.poll() == (0, LEFT)
  ui.pump([pygame.event.Event(pygame.JOYHATMOTION, joy=0, hat=0, value=(0,0))])
  assert ui.poll() == (0, -LEFT)


#  ui.controller_plumbing[0].visit(DebugValves())

  ui.init_controllers()
  ui.clear()



  while True:
    pid, evid = ui.poll()
    if evid == PASS:
      pygame.time.wait(5)
    elif evid == QUIT:
      raise SystemExit
    elif evid == SCREENSHOT:
      for joy in range(len(ui.controllers)):
        print("[Controller %d]" % joy)
        print(repr(ui.controller_plumbing[joy]))
        ui.controller_plumbing[joy].visit(DebugValves())
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
