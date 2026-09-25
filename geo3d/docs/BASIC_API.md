# GEO3D BASIC: comandos CALL para programar 3D no MSX-BASIC

Especificação da API, versão final proposta. Nada está implementado: esta etapa só fixa o conjunto de comandos. O texto junta as três propostas (fácil, jogos e enxuta) e corrige todos os bloqueios que as críticas encontraram.

## 1. Objetivo e princípios

**Objetivo:** um cartucho de extensão (ROM com cabeçalho "AB" e tratador de CALL) que deixa qualquer pessoa programar 3D no MSX-BASIC usando o geo3d. Quem começa não vê matriz, porta nem registrador; quem quer ir fundo continua alcançando o hardware.

- **Fácil primeiro.** Um cubo sólido, iluminado e girando sai em 4 linhas, e tudo tem valor padrão. As unidades são humanas: graus, porcentagem, velocidade por quadro e cores da paleta. Q2.14, portas, PORT#4, R#20/R#21, janela LRMM, limpeza e troca de páginas ficam por conta da ROM.
- **Uma instrução por quadro.** `CALL G3FRAME` move o que tem movimento automático, desenha tudo e mostra. O que o BASIC faz devagar (SIN/COS, laços por objeto, colisão, câmera em órbita) virou comando da ROM.
- **Seguro por construção.** Todo comando G3 devolve o controle com o geo3d parado e o motor de comandos do VDP livre, então o BASIC mistura LINE, COPY, PRINT #1 e PUT SPRITE com o 3D sem nenhuma regra extra. Os erros são os do próprio BASIC, então ON ERROR GOTO funciona.
- **Um programa, dois perfis.** Uma ROM só detecta sozinha a posição da chave DIP (88h ou 98h). Um programa que usa só comandos G3 roda igual nos dois perfis.
- **Cresce em níveis.** Há 11 comandos essenciais, depois os avançados, separados por grupo, e por fim uma camada de especialista com acesso direto ao V9968 e ao geo3d.

Forma dos nomes: `CALL G3xxx` ou a forma curta `_G3xxx`, sempre com o prefixo G3 e até 8 letras.

## 2. Perfis 98h e 88h

**98h:** o V9968 é o VDP do micro (portas 98h-9Ch) e o geo3d fica em 9Dh/9Fh. SCREEN, LINE, COPY, PRINT #1 e PUT SPRITE do BASIC desenham no mesmo V9968 que o geo3d. É o perfil que o openMSX emula (o fork com o geo3d). Em hardware real, ele exige um micro com o VDP interno desligado.

**88h:** o V9968 fica ao lado do VDP do micro, com imagem própria no HDMI do cartucho, e o geo3d fica em 8Dh/8Fh. Os comandos gráficos do BASIC continuam indo para o VDP interno (a TV). É o perfil do cartucho real hoje.

| Recurso | 98h | 88h |
|---|---|---|
| Comandos G3 (3D, texto, fundo, paleta) | sim | sim |
| Onde aparece o 3D | vídeo do MSX | HDMI do cartucho |
| LINE, PAINT, COPY, PRINT #1 sobre o 3D | sim, entre G3SCENE e G3FLIP | não (vão para a TV) |
| PUT SPRITE sobre o 3D | sim | não na v1 |
| Textura desenhada pelo BASIC (G3TEX) | sim, da página ativa do V9968 | sim, copiada do VDP interno |
| Figura de fundo (G3BG) | página 2 ou 3, viva | copiada na chamada (cerca de 0,3 s) |
| Texto na imagem 3D | PRINT #1 ou G3TEXT | só G3TEXT |
| LIST, INPUT, mensagens de erro | na mesma tela | na TV, com o 3D no HDMI |
| Sincronia da troca de página | gancho H.TIMI | a ROM lê o bit F do V9968 |
| Micro MSX1 | não | sim, mas sem G3TEX e G3BG |
| Onde testar hoje | openMSX (fork) | cartucho real |

**Detecção.** Na partida e em G3INIT, a ROM:
- procura primeiro em 88h e depois em 98h;
- identifica o geo3d por leituras antes de qualquer escrita de teste;
- em 98h, aceita o V9968 com ID 2 (modo V9958) ou 3 (modo V9968), para que um segundo G3INIT, feito com o V9968 já em modo V9968, também o encontre.

`CALL G3INIT(,&H88)` ou `CALL G3INIT(,&H98)` força um dos perfis.

**Recomendação:**
- Uma ROM e a mesma API, com detecção automática.
- Desenvolver e testar primeiro no 98h (openMSX) e validar depois no 88h, no cartucho.
- Ensinar com programas que usam só comandos G3. Na documentação, o que só existe no 98h (desenho do BASIC sobre o 3D, PUT SPRITE) aparece marcado como "só 98h".
- No 88h, a TV serve de prancheta e de monitor: desenhe ou carregue figuras com o BASIC no VDP interno e passe-as ao V9968 com G3TEX ou G3BG. LIST e as mensagens de erro ficam na TV enquanto o 3D roda no HDMI.

## 3. Modelo de dados e unidades

### 3.1 Eixos e coordenadas
- X aponta para a direita, Y para cima e Z para dentro da tela. É o mesmo referencial da câmera do geo3d.
- As coordenadas do mundo e dos modelos são inteiros de -32768 a 32767. Valores reais são arredondados, e a ROM guarda frações de 1/256 para as velocidades.
- Os modelos prontos medem cerca de 100 unidades; o logo GEO3D mede cerca de 400.
- A câmera padrão fica em (0,0,-300), olhando para a origem. A essa distância, um objeto de 100 unidades ocupa uns 85 pixels em SCREEN 5.
- Plano distante: a ROM calcula a posição de cada objeto em relação à câmera em 32 bits e descarta o que fica a mais de cerca de 30000 unidades. Assim, um objeto distante nunca reaparece em lugar errado.
- Plano próximo: uma face ou aresta com algum vértice a menos de 16 unidades à frente da câmera some inteira, porque o geo3d ainda não recorta em 3D.

### 3.2 Ângulos e movimento
- Os ângulos são em graus e aceitam frações e qualquer valor (a volta completa é tirada sozinha). Internamente, 65536 = 360 graus.
- Ordem das rotações: R = Ry(ay) * Rx(ax) * Rz(az).
  - ay é o rumo: 90 vira a frente para +X (direita).
  - ax levanta a frente (o nariz sobe).
  - az rola: positivo inclina para a direita, visto de trás.
- A frente de todo objeto é o seu +Z. A nave pronta aponta o nariz para +Z.
- G3SPIN e G3VEL contam por quadro desenhado. Se um quadro atrasar, o jogo fica mais lento, como nos jogos da época.

### 3.3 Tamanho e zoom
- G3SIZE é em porcentagem: 100 = o tamanho do modelo.
- G3ZOOM também:
  - 100 dá cerca de 53 graus de campo horizontal em SCREEN 5;
  - 200 mostra os objetos duas vezes maiores.

### 3.4 Cores
- **SCREEN 5 e 7:** a cor é um número da paleta (0 a 15).
- **Rampas:** uma rampa são 7 tons seguidos, do escuro (c) ao claro (c+6). A luz escolhe o tom de cada face. Paleta padrão da ROM:
  - 0 preto;
  - 1 a 7 azul;
  - 8 a 14 laranja;
  - 15 branco.
- Com essa paleta, `G3COLOR(n,1)` dá azul sombreado e `G3COLOR(n,8)` dá laranja sombreado.
- Uma cor que não começa uma rampa (no padrão, 0 e 15) é chapada, sem sombra.
- G3RAMP cria rampas novas a partir de qualquer cor de 1 a 9. SCREEN 5 comporta duas rampas mais duas cores fixas; para mais matizes, use SCREEN 8.
- **SCREEN 8:** a cor vai de 0 a 255 (GRB 3-3-2) e é sempre sombreada. Somar o nível de luz a um byte GRB não produz um tom, então a ROM desenha os sólidos de SCREEN 8 como textura: ela monta na VRAM uma faixa de amostras (256 colunas x 7 tons) e aponta cada face para a sua cor. Isso também elimina o conflito com o bit 7 da cor da face, que no geo3d significa "face texturizada".
- `G3COLOR(n,-1)` volta às cores próprias do modelo.

### 3.5 Páginas e VRAM
A ROM faz o duplo buffer nas páginas 0 e 1: desenha numa enquanto mostra a outra.

SCREEN 5 (256 KB, 128 bytes por linha):

| Linhas | Uso |
|---|---|
| 0-211 (pág. 0) | quadro, limpo a cada desenho |
| 212-255 (pág. 0) | tabelas do BASIC (sprites, paleta): nunca limpas |
| 256-511 (pág. 1) | quadro |
| 512-1023 (pág. 2-3) | do usuário: fundo, desenho de texturas, figuras |
| 1024-1791 (pág. 4-6) | texturas (96 KB), fora do alcance do BASIC |
| 1792-2047 (pág. 7) | modelos próprios (32 KB) |

SCREEN 7 e 8 (256 bytes por linha, 4 páginas):

| Linhas | Uso |
|---|---|
| 0-211 (pág. 0) | quadro |
| 212-255 (pág. 0) | tabelas do BASIC (F000h-FAFFh) |
| 256-511 (pág. 1) | quadro |
| 512-895 | texturas |
| 896-1023 | modelos próprios |

Em SCREEN 7 e 8 não há página de fundo. Nenhum comando G3 escreve nas áreas reservadas, exceto as rotinas da própria ROM.

### 3.6 Modelos prontos
Ficam na ROM e vão direto para o geo3d, sem ocupar VRAM. Cada um já vem com cores, UV (a textura cobre cada face), arestas e raio de colisão.

| Nº | Modelo | Tamanho |
|---|---|---|
| 0 | pivô invisível (alvo, câmera, colisão) | nenhum |
| 1 | cubo | 8 v, 6 f |
| 2 | pirâmide | 5 v, 5 f |
| 3 | octaedro | 6 v, 8 f |
| 4 | cunha | 6 v, 5 f |
| 5 | cilindro de 8 lados | 16 v, 14 f |
| 6 | esfera 8x6 | 42 v, 48 f |
| 7 | toro 8x6 | 48 v, 48 f |
| 8 | nave (nariz em +Z) | cerca de 12 v, 12 f |
| 9 | chão xadrez 4x4, 400x400, em y=0 | 25 v, 16 f |
| 10 | logo GEO3D das demos | 168 v, 126 f |
| 11 | placa 100x100 virada para a câmera | 4 v, 1 f |
| 12 | estrelas (64 pontos) | 64 v, 64 arestas |

O chão (9) é sempre desenhado antes dos outros objetos. A placa (11) só aparece de frente e é o jeito mais simples de mostrar uma textura.

### 3.7 Modelos próprios (16 a 31)
- Até 255 vértices, 255 faces e 255 arestas por modelo.
- **Vértice:** x, y, z inteiros. Mantenha-os dentro de ±8000, para que não saturem depois da rotação.
- **Face:** um quadrilátero convexo e plano de 4 vértices; um triângulo repete o terceiro vértice (d = c).
  - Ordem dos cantos: anti-horária vista de fora, com Y para cima.
  - Por padrão, a ROM corrige sozinha a orientação de cada face, testando-a contra o centro do modelo. Isso acerta em formas convexas; em formas côncavas, desligue a correção e ordene os cantos à mão.
  - A cor segue as regras de 3.4. A normal é calculada pela ROM.
- **UV (opcional):** 4 pares de texels de 0 a 255 (u para a direita, v para baixo). Uma face com UV é texturizada nos estilos 2 e 3.
- **Arestas (opcionais):** sem elas, o estilo arame usa as bordas das faces. Só são obrigatórias num modelo feito apenas de arame (nf = 0).
- **Três jeitos de definir:**
  - G3DATA lê linhas DATA (o mais fácil);
  - G3MDL mais G3VTX, G3FACE, G3UV e G3EDGE define um item de cada vez (para modelos calculados);
  - G3ARR lê arrays (especialista).
- Os modelos ficam na área de modelos da VRAM, no formato do próprio geo3d: vértice 6 bytes, face 11, UV 8, aresta 2.

### 3.8 Texturas
- O slot 0 é a textura pronta da ROM. Os slots 1 a 8 são feitos com G3TEX a partir de um desenho do BASIC (LINE, PAINT, CIRCLE, BLOAD ,S).
- Cada slot guarda 7 cópias pré-sombreadas empilhadas, e o nível de luz da face escolhe a cópia. Também pode guardar uma cópia só, sem sombra.
- O mapeamento é afim (sem correção de perspectiva), como nos consoles da época.
- Cada objeto usa uma textura, porque o geo3d tem um só TEXX/TEXY por passada.

### 3.9 Limites

| Item | Limite |
|---|---|
| Objetos | 16 (números 1 a 16) |
| Modelos próprios | 16 (16 a 31), 32 KB no total |
| Por modelo | 255 vértices, faces e arestas |
| Texturas | 8 slots mais a pronta |
| Textura sombreada | 64x64 (SCREEN 5), 32x32 (7 e 8) |
| Janela 3D | até a tela inteira, 212 linhas |
| RAM tirada do BASIC | cerca de 2 KB |

## 4. Comandos essenciais

Nível 1 (algo girando na tela): G3INIT, G3OBJ, G3SPIN e G3FRAME. Nível 2: o resto desta tabela.

| Sintaxe | O que faz |
|---|---|
| `G3INIT [(modo [,base])]` | liga o 3D e zera a cena |
| `G3OBJ(n,m [,x,y,z])` | põe o modelo m na cena como objeto n |
| `G3POS(n,x,y,z [,ax,ay,az])` | posiciona (e opcionalmente gira) |
| `G3ROT(n,ax,ay,az)` | define os ângulos em graus |
| `G3SPIN(n,dax,day,daz)` | gira sozinho a cada quadro |
| `G3SIZE(n,p [,py,pz])` | tamanho em % |
| `G3COLOR(n,c)` | cor ou rampa do objeto |
| `G3STYLE(n,s [,t])` | arame, sólido ou textura |
| `G3CAM(x,y,z)` | move a câmera |
| `G3FRAME [(v)]` | anima, desenha e mostra um quadro |
| `G3END` | desliga o 3D e devolve a tela |

### G3INIT [(modo [, base])]
Primeiro comando de todo programa 3D. Pode ser chamado de novo a qualquer momento, e então recomeça do zero.

Argumentos:
- `modo`: 5 (padrão), 7 ou 8.
- `base`: &H88 ou &H98, para forçar o perfil.

Modo de tela:
- **98h:** vale o SCREEN atual do BASIC (5, 7 ou 8). Em modo texto, dá Illegal function call: comece o programa com `SCREEN 5`. Um `modo` diferente do SCREEN atual também dá Illegal function call.
- **88h:** a ROM programa o V9968 sozinha (modo, 212 linhas, sprites e interrupções do V9968 desligados) e não mexe no VDP interno. Sem `modo`, usa o SCREEN atual se ele for 5, 7 ou 8, e senão usa 5. Num MSX1, use `CALL G3INIT(5)` sem `SCREEN 5`.

Nos dois perfis, a ROM:
1. espera o geo3d e o VDP ficarem livres;
2. destrava o PORT#4, faz R#21 = 0 (modo V9968: LRMM e 256 KB), R#20 = 1 (comandos rápidos), abre a janela LRMM em toda a VRAM (R#51-58) e trava de novo;
3. faz a borda R#7 = 0 e carrega a paleta padrão (3.4);
4. limpa as linhas 0-211 das páginas 0 e 1, mostra a 0 e passa a desenhar na 1;
5. configura o geo3d para a tela (W, H, CX, CY, F, ZNEAR) e copia a textura 0;
6. apaga os objetos, os modelos 16-31 e as texturas 1-8;
7. volta tudo ao padrão:
   - câmera em (0,0,-300), olhando para a origem, zoom 100;
   - luz vinda de cima, à esquerda e da frente (-1,1,-1);
   - fundo na cor 0, ritmo de 2 brancos por quadro, janela na tela inteira.

Erros: Device I/O error se o geo3d ou o V9968 não responderem; Illegal function call para um modo ou base inválidos.

### G3OBJ(n, m [, x, y, z])
- `n`: número do objeto, 1 a 16. Um objeto n que já exista é substituído.
- `m`: 0 (pivô), 1 a 12 (prontos) ou 16 a 31 (próprios).

Estado inicial do objeto:
- posição (x,y,z), com (0,0,0) como padrão;
- ângulos 0, sem giro nem velocidade, tamanho 100;
- as cores do modelo, estilo sólido (arame, se o modelo não tiver faces) e visível.

O objeto aparece a partir do próximo G3FRAME. Este comando não acessa o VDP.

Erro: Illegal function call se n ou m estiverem fora da faixa, ou se m for um modelo próprio vazio.

### G3POS(n, x, y, z [, ax, ay, az])
- Define a posição absoluta no mundo. Com os três ângulos opcionais, também faz o papel de G3ROT, o que economiza um CALL por quadro.
- Depois de G3LINK, a posição passa a ser relativa ao objeto pai.
- Vale a partir do próximo quadro.

Erro: Overflow fora de -32768 a 32767.

### G3ROT(n, ax, ay, az)
- Define os ângulos em graus (ver 3.2) e substitui os que o G3SPIN acumulou. O giro automático continua a partir dos novos ângulos.

### G3SPIN(n, dax, day, daz)
- A cada quadro desenhado, soma estes graus aos ângulos do objeto. Aceita frações.
- `G3SPIN(n,0,0,0)` para o giro.

### G3SIZE(n, p) ou G3SIZE(n, px, py, pz)
- `p` vai de 10 a 1600 (%), com padrão 100.
- A escala uniforme é exata: a ROM divide a posição relativa à câmera, em vez de mexer na matriz Q2.14.
- A forma com três valores estica um eixo por vez (por exemplo, um cubo vira parede). Nesse caso, o sombreamento das faces esticadas é aproximado.
- O tamanho também vale para o raio de colisão (G3HIT).

### G3COLOR(n, c)
- `c` é uma rampa (1 ou 8 no padrão, ou uma criada por G3RAMP): cada face fica entre c e c+6, conforme a luz, e o arame usa c+6.
- `c` que não é rampa: cor chapada, sem sombra.
- `c = -1`: volta às cores do modelo.
- No estilo textura, a cor vale para as faces sem UV.
- SCREEN 8: c vai de 0 a 255.

### G3STYLE(n, s [, t])

| s | Estilo |
|---|---|
| 0 | arame (LINE) |
| 1 | sólido sombreado |
| 2 | textura (LRMM) |
| 3 | textura com a cor 0 transparente (recortes) |

- `t`: slot de textura de 0 a 8, com padrão 0 (a textura pronta). As faces sem UV continuam sólidas.

Erro: Illegal function call se o slot estiver vazio.

### G3CAM(x, y, z)
- Põe a câmera nesta posição. Ela continua olhando para o alvo (G3LOOK; no padrão, a origem).
- Cancela G3CHASE.

### G3FRAME [(v)]
A instrução do quadro. Ela:
1. avança G3SPIN, G3VEL, G3LINK e G3CHASE;
2. limpa a página oculta (conforme G3BG) e desenha nela todos os objetos visíveis, do mais longe para o mais perto;
3. marca a troca de página para o primeiro branco vertical que venha pelo menos `v` brancos depois da troca anterior.

Ritmo:
- `v` tem padrão 2 (30 quadros por segundo em 60 Hz, 25 em 50 Hz).
- O valor informado fica guardado para os próximos quadros.

Retorno, por perfil:
- **98h:** volta logo depois do desenho, com o geo3d parado, e a troca de página acontece no branco. O BASIC segue trabalhando enquanto espera; o quadro seguinte só espera se ainda precisar da página.
- **88h:** espera o branco do V9968 e troca a página antes de voltar.

Se o programa trocou de SCREEN ou de página, a ROM percebe (lê SCRMOD e DPPAGE) e se ajusta sem perder a cena. Em modo texto, dá Illegal function call.

Forma curta: `_G3FRAME`.

### G3END
Desliga o 3D:
- espera o geo3d terminar;
- volta o V9968 ao modo V9958 (R#21 bit 0 = 1, R#20 = 0, e trava de novo);
- restaura a paleta do MSX, a página 0 e as cores do BASIC;
- desativa o gancho de interrupção. O gancho continua instalado, mas inerte, para não cortar outras extensões encadeadas.

Depois disso, só G3INIT funciona. Os demais comandos dão Illegal function call.

É recomendado no fim do programa. Se for esquecido, nada quebra (ver 7.1).

## 5. Comandos avançados

### 5.1 Objetos e movimento

| Sintaxe | O que faz |
|---|---|
| `G3VEL(n,dx,dy,dz)` | velocidade no mundo, por quadro |
| `G3VEL(n,s)` | velocidade s para a frente do objeto |
| `G3HIDE(n [,h])` | esconde (h=1) ou mostra (h=0) |
| `G3LINK(n,p)` | prende n ao objeto pai p |

**G3VEL:**
- Na forma de dois argumentos, a direção acompanha os ângulos atuais, então a nave voa para onde aponta.
- Guarda frações de 1/256.
- `G3VEL(n,0)` para o objeto.

**G3HIDE:**
- Um objeto escondido continua se movendo, mas não é desenhado nem testado por G3HIT.
- O pivô (modelo 0) nunca é desenhado, mas participa de G3HIT.

**G3LINK:**
- A posição e os ângulos de n passam a ser relativos a p (torre de tanque, roda, passageiro).
- Há um nível só: p não pode estar preso a outro objeto.
- `G3LINK(n,0)` solta n no lugar onde ele está.
- Custa cerca de 30 multiplicações a mais por quadro.

Erro: Illegal function call se p já estiver preso a outro objeto.

### 5.2 Câmera e luz

| Sintaxe | O que faz |
|---|---|
| `G3LOOK(x,y,z)` | câmera olha sempre para este ponto |
| `G3LOOK(n)` | câmera segue o objeto n com o olhar |
| `G3ORBIT(rumo,alt,d)` | câmera em órbita do alvo |
| `G3CHASE(n [,d,a,s])` | câmera atrás do objeto (ou nele) |
| `G3ZOOM(p)` | zoom em % |
| `G3LIGHT(x,y,z)` | direção de onde vem a luz |

**G3LOOK:**
- Guarda o alvo. A cada quadro, a ROM calcula o rumo e a inclinação da câmera (por tabela de atan, sem rolagem).
- Cancela G3CHASE.

**G3ORBIT:**
- Põe a câmera a `d` unidades do alvo, na posição alvo + d*(-sen(rumo)*cos(alt), sen(alt), -cos(rumo)*cos(alt)), olhando para ele.
- `G3ORBIT(0,0,300)` é a vista padrão; `alt` 30 olha de cima.
- Troca por um único CALL o SIN/COS em dupla precisão que o BASIC levaria milissegundos para calcular.

**G3CHASE:**
- `d` é a distância atrás do objeto (padrão 300) e `a` é a altura (padrão 100).
- `s` suaviza a curva, de 0 (rígido) a 7, com padrão 2.
- `d = 0` dá primeira pessoa: a câmera fica no objeto, na altura `a`, com os ângulos dele. Para andar em primeira pessoa, use um pivô com G3ROT e G3VEL(n,s).
- `G3CHASE(0)` desliga, e a câmera fica onde está.
- G3CAM, G3LOOK e G3ORBIT cancelam o G3CHASE.

**G3ZOOM:**
- `p` vai de 10 a 1000, com padrão 100. Pode mudar a cada quadro sem custo.

**G3LIGHT:**
- É o vetor que aponta para a luz, em coordenadas do mundo e de qualquer comprimento; a ROM normaliza.
- Nível da face = min(6, 7 * max(0, L.N)), o que dá 7 tons.
- `G3LIGHT(0,0,0)` desliga a sombra: cada face fica no tom mais claro da sua rampa.

### 5.3 Cor e fundo

| Sintaxe | O que faz |
|---|---|
| `G3RAMP(c,r,g,b)` | cria uma rampa de 7 tons em c..c+6 |
| `G3PAL(c,r,g,b)` | muda uma cor da paleta do V9968 |
| `G3BG(c [,p])` | fundo de cada quadro: cor, figura ou nada |

**G3RAMP:**
- `c` vai de 1 a 9, e r, g, b de 0 a 7. O tom k vale cor*(k+2)/8, arredondado.
- Registra c como rampa e desfaz o registro das rampas que ele sobrepõe.
- No 98h, também grava na tabela de paleta do BASIC, então COLOR=RESTORE a mantém.
- Em SCREEN 8, dá Illegal function call.

**G3PAL:**
- Funciona como COLOR=(c,r,g,b), mas vale nos dois perfis. No 88h, COLOR= só muda o VDP interno.

**G3BG:**
- `c` é a cor de fundo, com padrão 0. `c = -1` não limpa, o que deixa rastros.
- `p` = 2 ou 3 (só SCREEN 5): cada quadro começa com uma cópia (HMMM) dessa página.
  - **98h:** é a página viva do BASIC; desenhe nela com SET PAGE ,p ou carregue-a com BLOAD ,S.
  - **88h:** a ROM copia a página p do VDP interno no momento da chamada. Depois de mudar a figura, chame de novo.

### 5.4 Quadro, janela e texto

| Sintaxe | O que faz |
|---|---|
| `G3SCENE [(p)]` | desenha sem mostrar |
| `G3FLIP [(v)]` | mostra a página preparada |
| `G3VIEW(w,h [,y])` | limita o 3D a uma janela |
| `G3TEXT(x,y,e [,c,b])` | escreve texto ou número na imagem |

**G3SCENE:**
- Sem `p`, é a primeira metade de G3FRAME: avança os movimentos, limpa e desenha na página oculta, sem mostrar. No 98h, a ROM aponta ACPAGE para essa página, então LINE, PSET, COPY e PRINT #1 desenham por cima do 3D antes do G3FLIP.
- Com `p`, desenha os objetos visíveis na página p sem limpar e sem avançar os movimentos. Serve para "assar" um cenário 3D fixo numa página e usá-la depois como fundo em G3BG(0,p).

**G3FLIP:**
- É a segunda metade de G3FRAME. `v` segue a mesma regra de G3FRAME.
- `v = 0` troca na hora e pode rasgar a imagem.

**G3VIEW:**
- A janela 3D tem `w` x `h` pixels, encostada à esquerda e começando na linha `y` (padrão 0).
- Só a janela é limpa a cada quadro. O que fica fora dela (placar, moldura) permanece nas duas páginas.
- Não há deslocamento em X, porque o recorte do geo3d começa na coluna 0.

Erro: Illegal function call se y+h passar de 212 ou w passar da largura da tela.

**G3TEXT:**
- `e` pode ser um texto ou um número; o número sai como em STR$, sem o espaço inicial.
- Usa a fonte 8x8 do MSX. `c` tem padrão 15; `b` tem como padrão a cor de G3BG, e `b = -1` deixa o fundo transparente.
- Escreve na página oculta, então use entre G3SCENE e G3FLIP para texto sobre o 3D. Texto inteiramente fora da janela G3VIEW vai para as duas páginas e fica.
- No 88h, é o único jeito de pôr texto no HDMI.
- Cada caractere custa cerca de 0,25 ms: reescreva só o que mudou.

### 5.5 Modelos próprios

| Sintaxe | O que faz |
|---|---|
| `G3DATA(m [,o])` | lê o modelo m inteiro das linhas DATA |
| `G3MDL(m,nv,nf [,ne,o])` | cria um modelo vazio |
| `G3VTX(m,i,x,y,z)` | define o vértice i |
| `G3FACE(m,i,a,b,c,d [,k])` | define a face i (cor k) |
| `G3UV(m,i,u0,v0,u1,v1,u2,v2 [,u3,v3])` | coordenadas de textura da face i |
| `G3EDGE(m,i,a,b)` | define a aresta i |
| `G3TITLE(m,s$)` | transforma uma palavra em modelo 3D |

**G3DATA:**
- Lê a partir da posição atual do READ; use `RESTORE linha` antes. Formato:
  1. `nv, nf, ne, t` (t = 1 se houver UV);
  2. nv vezes `x, y, z`;
  3. nf vezes `a, b, c, d, cor` (um triângulo repete o c);
  4. ne vezes `a, b`;
  5. se t = 1, nf vezes `u0, v0, u1, v1, u2, v2, u3, v3`.
- Depois do comando, o READ continua logo após o modelo, então vários modelos podem vir em sequência.
- Erros: Out of DATA, Syntax error (valor que não é número), Illegal function call (índice inválido) e Out of memory (área de modelos cheia).

**Opção o** (em G3DATA e G3MDL), somando valores:
- 1: a ROM corrige a orientação das faces. Já vem ligada.
- 2: modelo de fundo, desenhado antes dos outros, como o chão.
- 0: desliga as duas.

**G3MDL:**
- Reserva espaço para nv vértices, nf faces e ne arestas, tudo zerado. Com ne = 0, as arestas saem das faces.
- Redefinir m libera o modelo antigo, e os objetos que o usam passam a mostrar o novo.

**G3VTX:**
- Pode ser chamado a qualquer momento: a ROM reenvia só os vértices que mudaram, o que permite ondas e deformações.
- As faces que tocam um vértice alterado têm a normal recalculada, a cerca de 1 a 3 ms por face. Para animar vértices, prefira modelos pequenos ou o estilo arame.

**G3FACE:**
- `d` é obrigatório; num triângulo, repita o c.
- `k` tem padrão 1.
- A ROM calcula a normal e, com a opção 1, corrige a orientação.

**G3UV:**
- Texels de 0 a 255. Ao receber UV, a face passa a ser texturizada.

**G3EDGE:**
- Só é obrigatório em modelo sem faces.

**G3TITLE:**
- Monta blocos extrudados numa fonte parecida com a do logo GEO3D: A-Z, 0-9, espaço e - . ! ?
- Cabem cerca de 6 letras (255 vértices). A frente tem a cor 8 e as laterais a cor 1.

Erro: Illegal function call se o texto não couber.

### 5.6 Texturas

| Sintaxe | O que faz |
|---|---|
| `G3TEX(t,x,y,w,h [,s])` | transforma um desenho do BASIC na textura t |

**G3TEX:**
- Copia o retângulo `w` x `h` na posição (x,y) da página ativa do BASIC para o slot `t` (1 a 8):
  - **98h:** a página ACPAGE do V9968;
  - **88h:** a página ACPAGE do VDP interno, que precisa estar no mesmo modo SCREEN.
- `s = 1` (padrão): grava 7 cópias pré-sombreadas. A cópia k escurece cada cor de rampa em 6-k tons, sem passar da base da rampa; as outras cores não mudam. Em SCREEN 8, a ROM escala R, G e B.
- `s = 0`: uma cópia só, sem sombra.
- Dica: desenhe com o tom mais claro de cada rampa (7 e 14 no padrão), para as cópias terem para onde escurecer.

Erro: Out of memory quando a área de texturas enche.

### 5.7 Consultas

| Sintaxe | O que faz |
|---|---|
| `G3PROJ(x,y,z,SX,SY [,V])` | onde um ponto aparece na tela |
| `G3PROJ(n,SX,SY [,V])` | onde o objeto n aparece na tela |
| `G3WHERE(n,X,Y,Z [,AX,AY,AZ])` | posição e ângulos atuais do objeto |
| `G3HIT(n,H [,a,b])` | colisão de n com os objetos a..b |
| `G3INFO(k,V)` | estado e estatísticas |

**G3PROJ:**
- Usa a câmera do último quadro. O cálculo é feito pela ROM, sem mexer no geo3d e sem esperar.
- `V = -1` se o ponto está na tela; `V = 0` se está fora, atrás da câmera ou escondido.
- Serve para pôr um PUT SPRITE (98h) ou um texto sobre um objeto, e para mirar.

**G3WHERE:**
- Devolve a posição depois do movimento automático e dos vínculos, e opcionalmente os ângulos (0 a 359.99).

**G3HIT:**
- Compara esferas: o raio do modelo vezes o tamanho.
- `H` recebe o menor número de objeto entre a e b (padrão 1 a 16), diferente de n e visível, que encosta em n; 0 se nenhum encosta.
- Um único CALL por quadro testa todas as colisões de n.

**G3INFO**, valores de k:

| k | V recebe |
|---|---|
| 0 | base das portas (&H88 ou &H98) |
| 1 | página oculta |
| 2 | página mostrada |
| 3 | brancos entre as duas últimas trocas (60/V = qps) |
| 4 | faces ou arestas desenhadas no último quadro |
| 5 | descartadas (costas ou fora da tela) |
| 6 | puladas pelo plano próximo |
| 7 | objetos desenhados |
| 8 | KB livres na área de modelos |
| 9 | KB livres na área de texturas |
| 10 | versão da ROM |
| 11 | recursos extras do geo3d (ver decisão 1) |
| 12 | contador de quadros |

As variáveis de saída de todos os comandos podem ser de qualquer tipo numérico (%, ! ou #).

### 5.8 Especialista

| Sintaxe | O que faz |
|---|---|
| `G3ARR(m,k$,i,n,A(e))` | carrega itens de um array |
| `G3TABLE(O%(0,0))` | liga um array de objetos ao quadro |
| `G3REG(r,v)` | escreve um registrador do V9968 |
| `G3VDP(sx,sy,dx,dy,nx,ny,clr,arg,cmd)` | comando cru do V9968 |

**G3ARR:**
- `k$` diz o que cada item traz:
  - "V": x, y, z;
  - "F": a, b, c, d, cor;
  - "U": 8 valores de UV;
  - "E": a, b.
- Lê os itens i..i+n-1 de elementos seguidos, a partir de A(e). Num array de várias dimensões, o primeiro índice varia mais rápido.
- Arrays inteiros (%) são o caminho rápido; ! e # também são aceitos, mais devagar.
- Um G3ARR por quadro anima uma malha inteira.

Erro: Subscript out of range se o array for curto.

**G3TABLE:**
- Liga ao quadro um array `DIM O%(7,16)`. O objeto n usa os campos O%(0..7,n):
  - 0 a 2: X, Y, Z;
  - 3 a 5: ângulos em graus;
  - 6 e 7: SX, SY, gravados pela ROM (-32768 se o objeto não apareceu).
- G3FRAME lê as posições e os ângulos direto do array, aplica G3VEL e G3SPIN, grava os resultados de volta e não precisa de nenhum CALL por objeto. Mudar `O%(0,3)=X` custa como qualquer atribuição do BASIC.
- A ROM reencontra o array pelo nome, mesmo que ele mude de lugar na memória.
- `G3TABLE` sem argumento desliga. RUN, CLEAR e ERASE também desligam.

**G3REG:**
- `r` vai de 0 a 58, com regras:
  - 15 e 17 são recusados (a ROM e o BIOS os usam);
  - 20 e 21 são escritos com o PORT#4 destravado e travado em volta;
  - de 32 a 58, espera o motor de comandos ficar livre;
  - no 88h, força IE0 = IE1 = 0;
  - no 98h, escreve com as interrupções desligadas e atualiza as cópias do BIOS (RG*SAV).
- Um comando com transferência pela CPU (HMMC, LMMC, LMCM) escrito em R#46 é recusado.

**G3VDP:**
- Y é absoluto: página*256 + y. Os argumentos omitidos valem 0.
- Espera o geo3d e o motor de comandos, dispara o comando e volta. O próximo comando G3 espera o término.
- HMMC, LMMC e LMCM dão Illegal function call.
- Exemplos de `cmd`: &HC0 HMMV, &HD0 HMMM, &H98 LMMM com TIMP, &H70 LINE, &H50 PSET.
- No 88h, é o jeito de desenhar 2D no HDMI.

**Acesso direto ao geo3d:**
- `G3INFO(0,P)` dá a base das portas: o índice e o status ficam em P+5 e os dados em P+7.
- Como todo comando G3 volta com o geo3d parado, `OUT` e `INP` do BASIC podem ser usados. Só não escreva enquanto `INP(P+5) AND 1` indicar RUN ocupado.
- A ROM reescreve os registradores dela a cada quadro.
- No 98h, não escreva na porta 99h com OUT (a interrupção do BIOS corrompe a sequência); use VDP(n)= ou G3REG.

## 6. Exemplos completos em BASIC

### 6.1 Objeto girando em 4 linhas

```basic
10 SCREEN 5:CALL G3INIT
20 CALL G3OBJ(1,1)
30 CALL G3SPIN(1,1,2,0)
40 CALL G3FRAME:GOTO 40
```

- O resultado é um cubo azul, sólido, iluminado de cima e da esquerda, a 30 quadros por segundo. CTRL+STOP sai.
- Troque o 1 de `G3OBJ(1,1)` por 10 e acrescente `CALL G3STYLE(1,2)` para ver o logo GEO3D com textura.
- No 88h, o mesmo programa mostra o cubo no HDMI. Num MSX1, troque a linha 10 por `10 CALL G3INIT(5)`.

### 6.2 Modelo próprio via DATA

Uma casa (caixa com telhado de duas águas), girando. Tecla 1 = arame, tecla 2 = sólido.

```basic
10 ' Casa em DATA: 1 = arame, 2 = solido
20 SCREEN 5:CALL G3INIT
30 RESTORE 100:CALL G3DATA(16)
40 CALL G3OBJ(1,16):CALL G3SPIN(1,0,2,0)
50 CALL G3CAM(0,120,-350)
60 CALL G3FRAME:K$=INKEY$
70 IF K$="1" THEN CALL G3STYLE(1,0)
80 IF K$="2" THEN CALL G3STYLE(1,1)
90 GOTO 60
100 DATA 10,8,0,0
110 DATA -50,-40,-50, 50,-40,-50, 50,40,-50, -50,40,-50
120 DATA -50,-40,50, 50,-40,50, 50,40,50, -50,40,50
130 DATA 0,90,-50, 0,90,50
140 DATA 0,1,2,3,1, 5,4,7,6,1, 4,0,3,7,1, 1,5,6,2,1
150 DATA 3,8,9,7,8, 2,6,9,8,8, 3,2,8,8,1, 6,7,9,9,1
```

- Linha 100: 10 vértices, 8 faces, 0 arestas (o arame usa as bordas das faces) e sem UV.
- Faces: as paredes na rampa 1 (azul), o telhado na rampa 8 (laranja) e as duas empenas como triângulos (o último vértice repetido).
- A orientação dos cantos não importa: a ROM corrige, porque a casa é convexa.

### 6.3 Vários objetos, textura desenhada pelo BASIC, duas páginas

Chão xadrez, quatro cubos com uma textura feita com LINE, CIRCLE e PAINT, câmera em órbita e uma legenda fixa abaixo da janela 3D. O duplo buffer nas páginas 0 e 1 é automático.

```basic
10 ' Cena com textura feita pelo BASIC
20 DEFINT A-Z:SCREEN 5:CALL G3INIT
30 SET PAGE 0,2:CLS
40 LINE(0,0)-(31,31),14,BF:LINE(4,4)-(27,27),7,BF
50 CIRCLE(15,15),8,15:PAINT(15,15),15
60 CALL G3TEX(1,0,0,32,32)
70 SET PAGE 0,0
80 CALL G3VIEW(256,192)
90 CALL G3OBJ(1,9,0,-60,0)
100 FOR I=2 TO 5
110 CALL G3OBJ(I,1,I*120-420,0,0):CALL G3SIZE(I,60)
120 CALL G3STYLE(I,2,1):CALL G3SPIN(I,0,I,1)
130 NEXT
140 CALL G3TEXT(8,200,"GEO3D BASIC",15)
150 A=(A+2) MOD 360:CALL G3ORBIT(A,25,500)
160 CALL G3FRAME:IF INKEY$="" THEN 150
170 CALL G3END
```

- Linhas 30 a 60: o desenho na página 2 (do V9968 no 98h, do VDP interno no 88h) vira a textura 1, com 7 tons de sombra.
- Linha 80: o 3D ocupa 256x192. A linha 140 escreve abaixo da janela, então o texto fica nas duas páginas e não é apagado.
- Linha 150: um único CALL move a câmera em órbita, sem SIN nem COS no BASIC.
- Por quadro, o BASIC executa só 3 instruções (linhas 150 e 160).

## 7. Cuidados técnicos

### 7.1 V9968, SCREEN e modo texto
- **O SCREEN do BASIC poderia desligar o modo V9968?** Não. A ROM escreve R#21 e R#20 com o PORT#4 destravado e trava de novo (PORT#4 = 80h). Com a trava, o V9968 ignora escritas em R#20/R#21, então SCREEN, COLOR e VDP()= não desligam o LRMM nem os 256 KB. Mesmo assim, a ROM atualiza as cópias RG20SAV/RG21SAV (FFF3h/FFF4h).
- **SCREEN ou SET PAGE feitos pelo programa:** o G3FRAME relê SCRMOD e DPPAGE a cada quadro e reaplica o que precisar, sem apagar a cena.
- **Efeitos visíveis do modo V9968 no BASIC:**
  - o VDP passa a informar ID 3;
  - o LINE e o COPY do próprio BASIC ficam mais rápidos (comandos rápidos);
  - o bit 3 de R#14 vira A17, mas o BIOS sempre o escreve com 0.
- **O que ainda falta verificar:**
  - que SCREEN 0 e 1 funcionam em modo V9968. Se não funcionarem, um gancho H.TOTE volta ao modo V9958 ao cair em modo texto, e o G3FRAME religa o modo V9968;
  - que as rotinas do MSX2+ que consultam o ID do VDP (SCREEN 10-12) não se confundem. SCREEN 10 a 12 ficam fora da API.
- **Programa que termina sem G3END:** a paleta padrão mantém a cor 4 azulada, então a tela de texto continua legível, e o gancho inerte não faz nada fora do modo gráfico.

### 7.2 Motor de comandos do VDP
- **Regra do geo3d:** não escrever R#32-R#46 com RUN ocupado. Todo comando G3 só volta com RUN desocupado, e o RUN só desocupa depois que o VDP termina o último comando. Com isso, o LINE, COPY, PAINT, PRINT #1 e PUT SPRITE do BASIC nunca encontram o geo3d no meio de um comando.
- Na direção contrária também não há risco: o geo3d espera CE = 0 sozinho, então um COPY do BASIC ainda em andamento quando G3FRAME começa não faz mal.
- **CTRL+STOP ou erro no meio de um quadro:** a ROM termina a passada em andamento, deixa o geo3d parado e R#15 = 0, e só então devolve o erro ou a parada ao BASIC.
- **Esperas:** todas têm prazo (cerca de 2 s, contado em voltas de laço e não em JIFFY) e terminam em Device I/O error. Elas também verificam CTRL+STOP.

### 7.3 Interrupções e troca de página
- **98h:**
  - A interrupção do BIOS lê S#0 a cada branco (o que zera o bit F) e supõe R#15 = 0. Por isso, a ROM nunca consulta S#0 nesse perfil.
  - Leituras de S#2 (CE) acontecem em janelas curtas com as interrupções desligadas (DI, R#15 = 2, IN, R#15 = 0, EI), repetidas a cada volta do laço.
  - Toda escrita de dois bytes na porta 99h (registrador ou endereço de VRAM) também é feita com as interrupções desligadas.
- **Tratador de CALL:** ele pode entrar com as interrupções desligadas (chamada entre slots). A ROM executa EI antes de qualquer espera; sem isso, o JIFFY para e a espera nunca acaba.
- **Troca de página no 98h:** é feita por um trecho em RAM na página 3, encadeado em H.TIMI. Esse gancho roda no começo da interrupção, antes do PLAY e da música, e por isso cai dentro do branco.
  - Ele escreve R#2 e atualiza RG2SAV, DPPAGE e ACPAGE.
  - Antes, confere se SCRMOD ainda é o modo gráfico: assim, uma troca pendente nunca bagunça a tela de texto depois de um erro.
- **88h:**
  - As interrupções do V9968 ficam desligadas (IE0 = IE1 = 0). O pino /INT do cartucho está ligado ao slot, e o BIOS nunca reconheceria essa interrupção, o que travaria o micro.
  - A ROM é a única que lê o S#0 do V9968. Ela zera o bit F logo depois de cada troca, para não confiar num F antigo, e conta os brancos por ele.
- **Envios longos pela CPU** (texturas, cópias do VDP interno) são feitos em blocos, com EI entre eles, para não perder teclado, JIFFY nem música.

### 7.4 VRAM
- Só as linhas 0-211 das páginas 0 e 1 são limpas. As tabelas de sprites e de paleta do BASIC (SCREEN 5: página 0, linhas 212-255; SCREEN 7 e 8: F000h-FAFFh) sobrevivem, então PUT SPRITE e COLOR=RESTORE continuam funcionando no 98h.
- Texturas e modelos ficam acima de 128 KB, onde VPOKE, BLOAD ,S, SET PAGE e os comandos gráficos do BASIC não chegam.
- G3VIEW não passa da linha 211. G3VDP e G3REG usam coordenadas absolutas, então quem os usa responde pelas áreas reservadas.

### 7.5 Paleta e borda
- Em SCREEN 5 e 7, a cor 0 é transparente e mostra a cor de borda (R#7). Se R#7 apontasse para a cor 4 (o padrão do BASIC), o fundo "preto" sairia colorido. Por isso, G3INIT faz R#7 = 0 e, no 98h, também zera RG7SAV, BDRCLR e BAKCLR, como faria um COLOR ,0,0. O bit TP de R#8 não é usado, porque tornaria opaca a cor 0 dos sprites.
- No 98h, a paleta é gravada pela SETPLT do SUB-ROM, que atualiza também a tabela do BASIC. No 88h, o BIOS nunca inicializa a paleta do V9968, e G3INIT carrega a paleta inteira.
- G3END devolve a paleta padrão do MSX.

### 7.6 ROM, RAM e ganchos
- **MegaROM ASCII8 de 64 KB:**
  - o banco fixo em 4000h-5FFFh guarda o tratador, o leitor de argumentos, a matemática e as tabelas de seno e atan;
  - os bancos de dados entram em 6000h-7FFFh: modelos prontos, fonte do G3TITLE, textura 0;
  - os modelos prontos vão desses bancos direto para o geo3d, via OTIR.
- **A ROM nunca mapeia a página 2 (8000h-BFFFh).** Ali ficam o programa, as variáveis, os arrays, as strings e às vezes a pilha do BASIC. Nenhuma imagem pode ter "AB" em 8000h, senão o BIOS a trataria como uma segunda ROM.
- **RAM de trabalho:** cerca de 2 KB na página 3, reservados no INIT abaixando HIMEM (o mesmo método do Disk ROM), com o ponteiro guardado em SLTWRK. Guardam:
  - base das portas e cópias de registradores;
  - 16 objetos e a câmera;
  - diretório de modelos e texturas;
  - lista de ordenação e contadores.

  Cada comando confere uma assinatura nessa área. Se ela sumiu (por exemplo, depois de uma ida ao MSX-DOS), o comando pede G3INIT.
- **Ganchos:** são trechos pequenos em RAM na página 3 (nunca chamadas entre slots a cada interrupção), sempre encadeados ao conteúdo anterior.
- Uma tecla segurada na partida impede a instalação (decisão 8).
- **Nomes:** o tratador rejeita na hora qualquer nome que não comece com G3, então o CALL de outras extensões quase não fica mais lento. Os nomes G3 são despachados por tabela.

### 7.7 Argumentos, variáveis e erros
- **Leitura dos argumentos** (conferido na ROM de teste, seção 11):
  - RST 08h (SYNCHR) e RST 10h (CHRGTR) **não servem**: eles levam ao BASIC na página 1, onde está a ROM, e travam a máquina. CHRGTR (4666h) passa por CALBAS, e SYNCHR vira "confere o caractere e chama CHRGTR";
  - FRMEVL, GETBYT, PTRGET, FRESTR e a rotina de erro (406Fh) passam por CALBAS;
  - rotinas da página 0 que pulam para a página 1 (FOUT, e FRCINT quando dá Overflow) não funcionam por CALBAS, que só troca a página do endereço chamado. Elas exigem um trampolim em RAM na página 3 que ponha o BASIC na página 1 antes da chamada, ou a conversão BCD feita pela própria ROM;
  - inteiros com sinal passam por FRMEVL + FRCINT (pelo trampolim), porque FRMQNT aceitaria 40000 sem dar erro;
  - variáveis sem DEFINT chegam como precisão dupla (VALTYP = 8): `X+2` e `A(0)` vieram assim no teste. A ROM aceita os três tipos numéricos em todo argumento;
  - com DEFINT A-Z (VALTYP = 2), há um caminho rápido sem conversão BCD.
- **Saídas:** a ROM lê e valida todas as entradas primeiro, calcula, e só então faz o PTRGET de cada saída e grava na mesma hora. Criar uma variável nova move os arrays, e isso invalidaria um endereço guardado.
- **Strings:** todo texto é liberado com FRESTR logo depois de lido, para não gerar "String formula too complex".
- **Erros usados:**
  - 2 Syntax error, 4 Out of DATA, 5 Illegal function call;
  - 6 Overflow, 7 Out of memory, 9 Subscript out of range;
  - 13 Type mismatch, 19 Device I/O error.

  Antes de gerar qualquer um deles, a ROM deixa o geo3d parado e R#15 = 0, então ON ERROR GOTO encontra um estado coerente.

### 7.8 Desempenho (estimativas a medir)

| Operação | Custo estimado |
|---|---|
| Um CALL com 4 argumentos | 1 a 3 ms (menos com DEFINT) |
| Matemática de um objeto que gira | 2 a 6 ms de Z80 |
| Objeto parado com câmera parada | quase zero (matriz reaproveitada) |
| Troca para o cubo no geo3d | menos de 1 ms |
| Troca para o logo (cerca de 3,5 KB) | 20 a 40 ms sem a decisão 1 |
| Logo texturizado (geo3d e VDP) | cerca de 4 ms (simulação) |

- **Resultado esperado:** 1 a 4 objetos a 30 quadros por segundo, e cerca de 8 objetos a 10-15 quadros por segundo.
- **Como a ROM ganha tempo:**
  - calcula a matriz do próximo objeto enquanto o geo3d ainda desenha o anterior (só a escrita dos registradores espera);
  - mantém todos os vértices residentes quando eles cabem em 255 e reenvia só as faces;
  - agrupa objetos do mesmo modelo quando a ordem de profundidade permite.
- **Conselho no manual:** DEFINT A-Z, poucos CALL por quadro, G3SPIN, G3VEL e G3ORBIT em vez de contas no BASIC, e G3TABLE para jogos com muitos objetos.

### 7.9 Limites do geo3d que aparecem no BASIC
- **Plano próximo:** uma face que cruza o plano próximo some inteira. O chão pronto é dividido em ladrilhos para que só os mais próximos sumam.
- **Ordem entre objetos:** segue a distância do centro de cada objeto; dentro de um objeto, o próprio geo3d ordena as faces. Objetos que se atravessam, ou muito grandes, podem sair na ordem errada. O chão e os modelos de fundo são desenhados primeiro.
- **Precisão:**
  - as normais das faces são calculadas com as diferenças reduzidas a 15 bits antes do produto vetorial, para não estourar 32 bits;
  - uma escala muito pequena, que precise ir para a matriz, compensa a luz até 2x; abaixo disso, a sombra fica um pouco mais escura.
- **SCREEN 7:** os pixels são estreitos. A ROM corrige a matriz, a posição (TX) e a luz juntas, e reaplica tudo quando o modo muda.

## 8. Decisões em aberto

1. **Extensões pequenas na RTL do geo3d antes da ROM.**
   - (a) Nenhuma: a ROM reenvia os modelos.
   - (b) Índices iniciais VFIRST, EFIRST e FFIRST, um deslocamento de cor COLOFS e um byte de versão (índice 49h, que hoje lê FFh).
   - (c) O item (b) mais um deslocamento X da janela (XOFS) e uma interrupção de "RUN terminou".

   **Recomendo (b).** Acaba com o reenvio de modelos (o logo junto de outro modelo cairia para uns 10 quadros por segundo), torna G3COLOR e G3LIGHT(0,0,0) gratuitos, e a API não muda: a ROM detecta pelo byte de versão (G3INFO 11). Antes, medir as LUTs, porque o Tang Nano 20K está quase cheio.
2. **Prefixo dos nomes.** G3 (curto: `_G3FRAME`) ou GEO. **Recomendo G3**, mas o primeiro passo do projeto deve ser uma ROM de teste que só imprime o PROCNM de cada nome, rodada com o BASIC real de MSX1, MSX2 e MSX2+. Ela confirma que o dígito e as palavras-chave dentro dos nomes (G3COLOR, G3POS, G3DATA, G3END) chegam intactos. Se algum falhar, troca-se o prefixo ou o nome.
3. **Onde fica a ROM.**
   - (a) Cartucho flash separado, MegaROM ASCII8 de 64 KB, como a ROM de demos.
   - (b) Dentro do cartucho V9968, se sobrar FPGA e memória.

   **Recomendo (a) na v1** e (b) como meta futura, que liberaria um slot.
4. **Modos de tela na v1.** **Recomendo** SCREEN 5 primeiro (é a referência: duas rampas e páginas de fundo), depois SCREEN 7 (correção de aspecto) e por fim SCREEN 8 (faixa de amostras). A API já prevê os três.

   **Revista em 24/09/2026:** ver a seção 10 (SCREEN 5 e SCREEN 8 com a paleta estendida do V9968).
5. **Quadro síncrono ou assíncrono.** **Recomendo síncrono na v1:** todo G3 volta com o geo3d parado, e só a troca de página fica agendada (98h). Desenhar enquanto o BASIC roda, alimentando o geo3d entre instruções, fica para depois, via a interrupção "RUN terminou" da RTL. Fazer isso pelo gancho H.NEWS tem pontos cegos e riscos de compatibilidade.
6. **Paleta padrão.** **Recomendo** 0 preto, 1-7 azul, 8-14 laranja e 15 branco. A cor 4 continua azulada, a tela de texto continua legível, a paleta vai também para a tabela do BASIC, e G3END devolve a paleta do MSX.
7. **G3INIT em modo texto no 98h.** Dar erro, ou trocar sozinho para SCREEN 5 via CHGMOD. **Recomendo dar erro** (Illegal function call): chamar CHGMOD por dentro deixa o estado gráfico do BASIC pela metade, e todo exemplo já começa com SCREEN 5.
8. **RAM de trabalho.** **Recomendo** cerca de 2 KB na página 3, abaixando HIMEM, com assinatura, e uma tecla segurada na partida (sugestão: G) para não instalar. A área logo acima de BOTTOM (página 2) fica descartada, porque o MSX-DOS a sobrescreve.
9. **Unidade de ângulo.** **Recomendo graus**, que são o que a pessoa já conhece. 256 por volta (estilo jogo) só se a medição mostrar que a conversão pesa; nesse caso, entraria como uma opção de G3INIT, e graus continuariam sendo o padrão.
10. **Tamanho da v1.** **Recomendo** que a v1 tenha os 11 essenciais, as seções 5.1 a 5.7 (menos G3LINK e G3TITLE), G3REG e G3VDP. A v1.1 traria G3LINK, G3TITLE, G3ARR, G3TABLE e a forma não uniforme de G3SIZE.
11. **2D e sprites no 88h.** **Recomendo** que a v1 fique com G3TEXT, G3BG e G3VDP. Se o 88h for o alvo principal dos usuários, a v1.1 ganharia G3LINE, G3BOX e G3PSET (relativos à página oculta) e G3SPRITE.
12. **Número de objetos.** 16 ou 32. **Recomendo 16:** ocupa cerca de 1 KB de RAM, e o Z80 não dá conta de muito mais a 30 quadros por segundo. Passar a 32 depois não muda a API.
13. **G3DATA lendo DATA direto.** **Recomendo sim**, porque é o jeito mais fácil de fazer um modelo próprio. O leitor de DATA seria da própria ROM, a partir de DATPTR e da rotina FIN do Math-Pack. Se ele se mostrar frágil em alguma versão do BASIC, o recurso é o laço de READ com G3VTX e G3FACE, que já estão previstos.
14. **Ordem de desenvolvimento e testes.** **Recomendo:**
    1. a ROM de teste de nomes;
    2. uma máquina openMSX com as ROMs reais de MSX2/2+ BASIC, o V9968 do fork e o geo3d (98h), já que o C-BIOS não tem BASIC;
    3. um ambiente de teste Z80 no estilo de `run_rom_z80.py` com uma ROM BASIC, para o 88h;
    4. o cartucho real.

## 9. Decisões tomadas (24/09/2026)

| Nº | Decisão | Resultado |
|---|---|---|
| 1 | Extensões pequenas na RTL do geo3d | **Sim**, opção (b): VFIRST, EFIRST, FFIRST, COLOFS e byte de versão. Medir as LUTs antes. |
| 3 | Onde fica a ROM | **ROM de extensão separada** (cartucho flash). |
| 14.2 | ROMs de MSX2/2+ BASIC para testar no openMSX | **Disponibilizadas** (pacote de ROMs de sistema do openMSX, fora do git). |
| 2 | Prefixo dos nomes | **G3**, conferido nos BASICs 1.0 Br, 2.x, 3.0 e 4.0 (seção 11). |
| 4 | Modos de tela na v1 | Em revisão: ver a seção 10. |

## 10. Recursos novos do V9968 e o que mudam no 3D

Os números de SCREEN são os mesmos do V9958 (0 a 12); os modos do RTL são G1 a G7, T1, T2 e mosaico (`vdp_timing_control_screen_mode.v`). O V9968 acrescenta recursos ligados pelo R#20 (`vdp_cpu_interface.v`), que na prática funcionam como modos novos:

| R#20 | Recurso | Efeito no 3D |
|---|---|---|
| bit 4 EPAL | Paleta estendida: 256 entradas de 15 bits (32 níveis por canal, 32768 cores). Em SCREEN 8, cada pixel passa a ser um índice dessa paleta (`vdp_color_palette.v`). O R#16 passa a ter 8 bits e cada cor é escrita na porta 2 em 3 bytes (R, G, B de 5 bits), com autoincremento. | **Muda o jogo:** SCREEN 8 com paleta de 256 cores comporta 36 rampas de 7 tons (cor base + nível de luz), com matizes livres. Resolve o problema do SCREEN 8 em GRB 3-3-2 da seção 3.4 sem a faixa de amostras. Em SCREEN 5, as 16 cores também ganham 32 níveis por canal. |
| bit 5 | Entrelaçado plano: 424 linhas com a VRAM em ordem linear | 3D em 512 x 424 (SCREEN 7 entrelaçado) numa página contínua, sem as linhas pares e ímpares em páginas separadas. |
| bit 3 SP3 | Modo de sprites 3 | Sprites sobre o 3D (HUD, naves) com mais recursos. |
| bit 7 S16 | 16 sprites por linha nos modos 1 e 2 | Idem. |
| bit 6 CEIE | Interrupção de fim de comando | Base para o desenho assíncrono (decisão 5). |
| bit 0 HS | Comandos rápidos | Já usados: sem eles, faces sólidas e texturas não cabem num quadro. |
| bits 1-2 | Sprites e interrupção de linha fora do R#23 | Cenas com rolagem vertical. |
| (R#21 bit 0 = 0) | Comandos LRMM e LFMC, 256 KB de VRAM | Já usados: texturas, e VRAM para texturas e modelos. |

**Cuidados:**
- Com EPAL, as cores de face de 128 a 255 esbarram no bit 7 "face com textura" do geo3d numa cena que misture textura e sólido. A extensão COLOFS (decisão 1) ou restringir as rampas sólidas às bases 0 a 127 (18 rampas) resolve.
- Com EPAL ligado, o formato de escrita da paleta muda (3 bytes em vez de 2), então o `COLOR=` e o `COLOR=RESTORE` do BASIC gravam lixo. G3END (e o tratamento de erro da ROM) desliga o EPAL antes de devolver a paleta ao BASIC.
- O `COLOR=` do BASIC só conhece 16 cores com 8 níveis; a paleta de 256 entradas e 32 níveis precisa de comandos da ROM (G3PAL, G3RAMP).
- O fork do openMSX já trata EPAL, SP3, S16 e o entrelaçado plano (`isEPAL`, `isSP3`, `isS16`, `isFIL` em `VDP.hh`), então dá para testar no perfil 98h. Falta conferir cada um contra a RTL.

**Proposta para a v1 (decisão 4, revista):** dois modos de primeira classe:
1. **SCREEN 5**, o mais compatível (duas rampas, páginas de fundo);
2. **SCREEN 8 com paleta de 256 cores (EPAL)**, o modo "bonito" do V9968 (dezenas de rampas, cores de 15 bits).

SCREEN 7 (e o entrelaçado 512 x 424) e os sprites SP3/S16 ficam para a v1.1.

## 11. ROM de teste de nomes: resultados (24/09/2026)

`geo3d/basic/g3names.asm` é uma ROM de 16 KB com um tratador de CALL que imprime o nome recebido em PROCNM, os bytes crus depois do nome e cada argumento avaliado pelo FRMEVL. `disk/G3TEST.BAS` chama os 46 nomes desta especificação e algumas variações; `run_names.sh` roda o programa no openMSX e grava um registro por máquina em `out/`.

**Máquinas** (ROMs reais de sistema, fork V9968 do openMSX):

| Máquina | BASIC | Registro |
|---|---|---|
| Gradiente Expert XP-800 + Microsol CDX-2 | 1.0 Br (MSX1) | 59 CALLs |
| Philips NMS 8245 | 2.x (MSX2) | idêntico |
| Panasonic FS-A1WSX | 3.0 (MSX2+) | idêntico |
| Panasonic FS-A1ST com V9968 | 4.0 (turboR) | idêntico |

Os quatro registros e os quatro programas tokenizados são iguais byte a byte.

**Resultados:**
- **Os 46 nomes chegam intactos**, com o dígito 3 e com palavras-chave dentro (G3COLOR, G3END, G3DATA, G3LINE, G3PSET, G3SPRITE, G3VDP, G3ORBIT). Depois de `CALL` ou `_`, o tokenizador guarda o nome como texto puro.
- `_G3OBJ(...)`, `IF 1 THEN CALL G3END` e `CALL G3FRAME:CALL G3END` funcionam.
- `call g3color(3)` é gravado em maiúsculas já na digitação: PROCNM = G3COLOR.
- `CALL G3COLOR (1,2)`, com espaço antes do parêntese, funciona.
- `CALL G3 COLOR(4)` chega como "G3 COLOR", com o espaço. A ROM trata como nome desconhecido.
- Um nome desconhecido (`CALL XYZ`), passado adiante com carry, vira Syntax error (ERR = 2) com o número de linha certo.
- **Constantes:** `&H88` é gravada como 0Ch 88h 00h. Há um byte 00 no meio do comando, então o texto só pode ser percorrido por CHRGTR, nunca procurando o fim da linha.
- **Tipos:** constantes inteiras chegam como inteiro (`-50`, `256`, `&HFFFF` = -1); `-3.5` e `1E+10` como precisão simples; `1#/3`, `X+2` e `A(0)` como precisão dupla. Strings (`"HELLO"`, `A$`) chegam pelo descritor e são liberadas com FRESTR.
- **Chamadas ao BASIC:** ver a seção 7.7. RST 08h/10h e FOUT por CALBAS travam a máquina; CHRGTR, FRMEVL, FRESTR e ERROR por CALBAS funcionam nos quatro BASICs, nos mesmos endereços.

**Perfis no openMSX** (nada disso vai para o git: as ROMs de sistema ficam em `~/.openMSX/share/systemroms` no WSL e em `openmsx-geo3d/share/systemroms` no Windows):
- **98h:** `-machine Panasonic_FS-A1ST_V9968 -ext geo3d`. É o turboR com o V9968 no lugar do VDP, e já vem no fork.
- **88h:** qualquer máquina com `-ext HRA_V9968 -ext geo3d88`. HRA_V9968 é o cartucho em 88h, que já vem no fork; `geo3d88.xml` é novo (geo3d em 8Dh/8Fh, ligado ao VDP "V9968"). Conferido no MSX1 Expert e no FS-A1WSX: geo3d responde, e o V9968 dá ID 3 depois de R#21 = 0.

**Próximo passo:** o esqueleto da ROM real, com INIT, RAM na página 3, trampolim para as rotinas da página 0, despacho por tabela e os comandos G3INIT e G3END. Testar nos mesmos quatro BASICs.
