# Code to construct pyDDR's menus

import pygame, menus, os, sys, copy

from constants import *
from announcer import Announcer
from gfxtheme import GFXTheme

# A simple on/off setting, 0 or 1
def get_onoff(name):
  if mainconfig[name]: return None, "on"
  else: return None, "off"

def switch_onoff(name):
  mainconfig[name] ^= 1
  return get_onoff(name)

# Rotate through a list of option strings
def get_rotate(name, list):
  return None, mainconfig[name]

def switch_rotate(name, list):
  try:
    new_i = (list.index(mainconfig[name]) + 1) % len(list)
    mainconfig[name] = list[new_i]
  except ValueError: mainconfig[name] = list[0]
  return get_rotate(name, list)

def switch_rotate_back(name, list):
  try:
    new_i = (list.index(mainconfig[name]) - 1) % len(list)
    mainconfig[name] = list[new_i]
  except ValueError: mainconfig[name] = list[-1]
  return get_rotate(name, list)

# Rotate through a list of option strings, but use the index as the value.
def get_rotate_index(name, list):
  return None, list[mainconfig[name]]

def switch_rotate_index(name, list):
  mainconfig[name] = (mainconfig[name] + 1) % len(list)
  return get_rotate_index(name, list)

def switch_rotate_index_back(name, list):
  mainconfig[name] = (mainconfig[name] - 1) % len(list)
  return get_rotate_index(name, list)

def fullscreen_toggle(dummy):
  mainconfig["fullscreen"] ^= 1
  pygame.display.toggle_fullscreen()
  return None, None

def get_tuple(name, list):
  for item in list:
    if item[0] == mainconfig[name]:
      return None, item[1]
  return None, "custom: " + str(mainconfig[name])

def switch_tuple(name, list):
  next = False
  for item in list:
    if next:
      mainconfig[name] = item[0]
      next = False
      break
    elif item[0] == mainconfig[name]:
      next = True
  if next: mainconfig[name] == list[0][0]
  return get_tuple(name, list)

def switch_tuple_back(name, list):
  list = copy.copy(list)
  list.reverse()
  return switch_tuple(name, list)

def do(screen, songselect, songdata):

  onoff_opt = { E_START: switch_onoff, E_CREATE: get_onoff }
  rotate_opt = { E_START: switch_rotate,
                 E_LEFT: switch_rotate_back,
                 E_RIGHT: switch_rotate,
                 E_CREATE: get_rotate }
  rotate_index_opt = { E_START: switch_rotate_index,
                       E_LEFT: switch_rotate_index_back,
                       E_RIGHT: switch_rotate_index,
                       E_CREATE: get_rotate_index }
  tuple_opt = { E_START: switch_tuple,
                E_LEFT: switch_tuple_back,
                E_RIGHT: switch_tuple,
                E_CREATE: get_tuple }

  m = (["Play Game", {E_START: songselect}, songdata],
       ("Game Options",
        ["Autofail", onoff_opt, ("autofail",)],
        ["Announcer", rotate_opt, ("djtheme", Announcer.themes())],
        ["Reverse", onoff_opt, ("reversescroll",)],
        ["Speed", rotate_opt, ('scrollspeed', [0.5, 1, 1.5, 2, 4, 8])],
        ["Little", rotate_index_opt,
         ("little", ("show all", "no 16ths", "no 8ths",
                             "no 8ths or 16ths"))],
        ["Hidden", rotate_index_opt,
         ("hidden", ("off", "hide one","hide two","hide three"))],
        ["Sudden", rotate_index_opt,
         ("sudden", ("off", "hide one", "hide two", "hide three"))],
        ["Top Arrows", onoff_opt, ("showtoparrows",)],
        ["Scale Arrows", rotate_index_opt,
         ("arrowscale", ("shrink", "normal", "grow"))],
        ["Spin Arrows", onoff_opt, ("arrowspin",)],
        ["Back", None, None]
        ),
       ("Graphic Options",
        ["Graphics", rotate_opt, ('gfxtheme', GFXTheme.themes())],
        ["Exploding", rotate_index_opt,
         ('explodestyle', ('none', 'rotate', 'scale', 'rotate & scale'))],
        ["Backgrounds", onoff_opt, ('showbackground',)],
        ["Brightness", tuple_opt, ('bgbrightness',
                                              [(32, 'very dark'),
                                               (64, 'dark'),
                                               (127, 'normal'),
                                               (192, 'bright'),
                                               (255, 'very bright')])],
        ["Lyrics", onoff_opt, ("showlyrics",)],
        ["Main Lyrics", rotate_opt, ("lyriccolor", lyric_colors.keys())],
        ["Other Lyrics", rotate_opt, ("transcolor", lyric_colors.keys())],
        ["LPS Counter", onoff_opt, ('fpsdisplay',)],
        ["Back", None, None]
        ),
       ["Sort By", rotate_index_opt,
        ("sortmode", ["file", "song", "group", "bpm",
                              "difficulty", "mix"])],
       ["Quit", None, None]
       )

  me = menus.Menu("Main Menu", m, screen)
  me.display()
