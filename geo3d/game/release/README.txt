VECTOR RAID - a side-scrolling shooter for the V9968 cartridge with geo3d
=========================================================================

Files
  GEO3D_SHOOTER_98.ROM   V9968 at ports 98h (openMSX: the V9968 is the
                         machine's VDP), geo3d at 9Dh/9Fh
  GEO3D_SHOOTER_88.ROM   V9968 cartridge at ports 88h (real hardware, the
                         DIP switch at 88h), geo3d at 8Dh/8Fh; the picture
                         comes out of the cartridge's HDMI port
Both are 64 KB MegaROMs, ASCII16 mapper. They are built from geo3d/game
(build.sh). In the openMSX fork with geo3d (github.com/alexmoncks/openMSX,
branch geo3d):
  openmsx -machine C-BIOS_V9968_JP -ext geo3d -cart GEO3D_SHOOTER_98.ROM -romtype ASCII16

Controls
  cursor keys or joystick 1     move the ship
  SPACE or trigger A (hold)     fire (autofire)
  SPACE on the title            start a game
  ESC                           back to the title
Without a key for 20 seconds the title starts the attract demo (the ship
plays itself); SPACE or trigger A starts a game from it, any other key
goes back to the title.

The game
  The intro shows a starfield, then the background tile scrolls in, then
  the foreground band (parallax: it moves twice as fast). Each round has 8
  waves of darts, saucers and rocks and ends with a gunship; the next round
  is faster. 3 lives, an extra life at 20,000 points and every 50,000 after.
  The ship and the enemies are 3D models drawn by geo3d (filled, shaded
  faces), 30 frames per second. Collisions come from invisible hardware
  sprites under the ship, the shots and the enemies (the VDP's sprite
  collision flag): a frame that raised the flag gets a box test on the
  positions it showed. On a VDP that does not report invisible sprites
  (the current openMSX fork) the game tests the same boxes itself every
  frame.

----------------------------------------------------------------------------
Portugues

  GEO3D_SHOOTER_98.ROM   V9968 nas portas 98h (openMSX)
  GEO3D_SHOOTER_88.ROM   cartucho V9968 nas portas 88h (hardware real)
  No openMSX (fork com o geo3d), use a linha de comando mostrada acima.

Controles
  setas ou joystick 1           movem a nave
  ESPACO ou botao A (segurar)   atira (tiro automatico)
  ESPACO na tela de titulo      comeca o jogo
  ESC                           volta ao titulo
Depois de 20 segundos no titulo sem tecla, comeca a demonstracao (a nave
joga sozinha); ESPACO ou o botao A comeca um jogo a partir dela, qualquer
outra tecla volta ao titulo.

A abertura mostra o campo de estrelas, depois entra o tile de fundo e em
seguida o tile da frente (paralaxe). A nave e os inimigos sao modelos 3D
desenhados pelo geo3d, a 30 quadros por segundo; as colisoes usam sprites
invisiveis (o flag de colisao do VDP).
