import pygame, announcer, colors, fontfx

from constants import *

# FIXME - This whole thing needs reworking/documentation

class GradingScreen:
  def __init__(self, judges):
    self.judges = judges

    for judge in judges:
      if judge == None: continue
      print "Player "+repr(judges.index(judge)+1)+":"
    
      grade = judge.grade()
      totalsteps = (judge.marvelous + judge.perfect + judge.great +
                    judge.ok + judge.boo + judge.miss)
      steps = (grade, judge.diff, totalsteps, judge.bestcombo, judge.combo)

      numholds = judge.numholds()
      goodholds = numholds - judge.badholds

      steptypes = (judge.marvelous, judge.perfect, judge.great, judge.ok,
                   judge.boo, judge.miss, goodholds, numholds)
      print ("GRADE: %s (%s) - total steps: %d best combo" +
             " %d current combo: %d") % steps
      print ("V: %d P: %d G: %d O: %d B: %d M: %d - %d/%d holds") % steptypes
      print
 
  def make_gradescreen(self, screen, background):
    judge = self.judges[0]

    if judge is None: return None

    totalsteps = (judge.marvelous + judge.perfect + judge.great +
                  judge.ok + judge.boo + judge.miss)

    if totalsteps == 0: return None

    # dim screen
    for n in range(31):
      background.set_alpha(255-(n*4))
      screen.fill(colors.BLACK)
      screen.blit(background, (0, 0))
      pygame.display.flip()
      pygame.time.wait(1)

    grading = fontfx.sinkblur("GRADING",64,4,(224,72),(64,64,255))
    grading.set_colorkey(grading.get_at((0,0)))
    screen.blit(grading, (320-grading.get_rect().centerx,-8) )
    pygame.display.update()

    rows = ["MARVELOUS", "PERFECT", "GREAT", "OK", "BOO",
            "MISS", "early", "late", " ", "TOTAL", " ", "MAX COMBO",
            "HOLDS", " ", "SCORE"]

    for j in range(4):
      for i in range(len(rows)):
        fc = ((j*32)+96-(i*8))
        if fc < 0: fc=0
        gradetext = fontfx.shadefade(rows[i],28,j,(224,32), (fc,fc,fc))
        gradetext.set_colorkey(gradetext.get_at((0,0)))
        gradetextpos = gradetext.get_rect()
        gradetextpos.right = 32 + screen.get_rect().centerx + 8-j
        gradetextpos.top = 64 + (i*24) + 8-j
        r = screen.blit(gradetext, (320-FONTS[28].size(rows[i])[0]/2,
                                64 + (i*24) + 8-j))
        pygame.display.update(r)
      pygame.time.wait(100)

    player = 0

    for judge in self.judges:
      grade = judge.grade()
      for i in range(4):
        font = pygame.font.Font(None, 100-(i*2))
        gradetext = font.render(grade, 1, (48 + i*16, 48 + i*16, 48 + i*16))
        gradetext.set_colorkey(gradetext.get_at((0,0)))
        r = screen.blit(gradetext, (200 + 250 * player - (font.size(grade))[0]/2, 150))
        pygame.display.update(r)
        pygame.time.delay(48)

      totalsteps = (judge.marvelous + judge.perfect + judge.great + judge.ok +
                    judge.boo + judge.miss)
      rows = [judge.marvelous, judge.perfect, judge.great, judge.ok,
              judge.boo, judge.miss, judge.early, judge.late]

      for j in range(4):
        for i in range(len(rows)):
          fc = ((j*32)+96-(i*8))
          if fc < 0: fc=0
          text = "%d (%d%%)" % (rows[i], 100 * rows[i] / totalsteps)
          gradetext = fontfx.shadefade(text,28,j,(FONTS[28].size(text)[0]+8,32), (fc,fc,fc))
          gradetext.set_colorkey(gradetext.get_at((0,0)))
          graderect = gradetext.get_rect()
          graderect.top = 72 + (i*24) - j
          if player == 0:
            graderect.left = 40
          else:
            graderect.right = 600
          r = screen.blit(gradetext, graderect)
          pygame.display.update(r)
        pygame.time.wait(100)

      # Total
      for j in range(4):
        gradetext = fontfx.shadefade(str(totalsteps),28,j,(FONTS[28].size(str(totalsteps))[0]+8,32), (fc,fc,fc))
        gradetext.set_colorkey(gradetext.get_at((0,0)))
        graderect = gradetext.get_rect()
        graderect.top = 288-j
        if player == 0:
          graderect.left = 40
        else:
          graderect.right = 600
        r = screen.blit(gradetext, graderect)
        pygame.display.update(r)
      pygame.time.wait(100)

      # Combo
      for j in range(4):
        text = "%d (%d%%)" % (judge.bestcombo, judge.bestcombo * 100 / totalsteps)
        gradetext = fontfx.shadefade(text,28,j,(FONTS[28].size(text)[0]+8,32), (fc,fc,fc))
        gradetext.set_colorkey(gradetext.get_at((0,0)))
        graderect = gradetext.get_rect()
        graderect.top = 336-j
        if player == 0:
          graderect.left = 40
        else:
          graderect.right = 600
        r = screen.blit(gradetext, graderect)
        pygame.display.update(r)
      pygame.time.wait(100)

      # Holds
      for j in range(4):
        text = "%d / %d" % (judge.numholds() - judge.badholds, judge.numholds())
        gradetext = fontfx.shadefade(text,28,j,(FONTS[28].size(text)[0]+8,32), (fc,fc,fc))
        gradetext.set_colorkey(gradetext.get_at((0,0)))
        graderect = gradetext.get_rect()
        graderect.top = 360-j
        if player == 0:
          graderect.left = 40
        else:
          graderect.right = 600
        r = screen.blit(gradetext, graderect)
        pygame.display.update(r)
      pygame.time.wait(100)

      # Score
      for j in range(4):
        gradetext = fontfx.shadefade(str(judge.score), 28, j,
                                     (FONTS[28].size(str(judge.score))[0]+8,32), (fc,fc,fc))
        gradetext.set_colorkey(gradetext.get_at((0,0)))
        graderect = gradetext.get_rect()
        graderect.top = 412-j
        if player == 0:
          graderect.left = 40
        else:
          graderect.right = 600
        r = screen.blit(gradetext, graderect)
        pygame.display.update(r)
      pygame.time.wait(100)

      player += 1

    background.set_alpha()

    return 1
    
  def make_waitscreen(self, screen):
    idir = -4
    i = 192
    screenshot = 0
    while 1:
      if i < 32:        idir =  4
      elif i > 224:     idir = -4

      i += idir
      ev = event.poll()
      if (ev[1] == E_QUIT) or (ev[1] == E_START):
        break
      elif ev[1] == E_FULLSCREEN:
        pygame.display.toggle_fullscreen()
        mainconfig["fullscreen"] ^= 1
      elif ev[1] == E_SCREENSHOT:
        print "writing next frame to", os.path.join(rc_path, "screenshot.bmp")
        screenshot = 1
          
      gradetext = FONTS[32].render("Press ESC/ENTER/START",1, (i,128,128) )
      gradetextpos = gradetext.get_rect()
      gradetextpos.centerx = screen.get_rect().centerx
      gradetextpos.bottom = screen.get_rect().bottom - 16
      r = screen.blit(gradetext,gradetextpos)
      pygame.display.update(r)
      pygame.time.wait(40)     # don't peg the CPU on the grading screen

      if screenshot:
        pygame.image.save(pygame.transform.scale(screen, (640,480)),
                          os.path.join(rc_path, "screenshot.bmp"))
        screenshot = 0

    return
