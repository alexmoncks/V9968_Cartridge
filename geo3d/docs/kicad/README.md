# Desenho lógico em KiCad: V9968 + geo3d v2.0 no Tang Nano 20K

Esquemático hierárquico do projeto FPGA gerado a partir do RTL.
Para ler, abra o `geo3d_logic.pdf`, que tem 13 páginas com links entre as folhas.
Para navegar no KiCad 10, abra o `geo3d_logic.kicad_pro`.

| Pág. | Folha | Nível de detalhe |
|---|---|---|
| 1 | Topo (`tangnano20k_vdp_cartridge`) | pinos do FPGA, folhas, IPs, cola do barramento em portas, ocupação |
| 2 | `clock_reset` | PLLs, CLKDIV e sincronizadores de reset (flip-flops) |
| 3 | `msx_slot` | portas lógicas e flip-flops, sinal a sinal |
| 4 | `geo3d_bus` | portas lógicas, os três domínios de clock e as travessias por toggle |
| 5 | `geo3d_engine` | blocos RTL: janela Z80, 6 RAMs, FSM, datapaths, emissor de comandos |
| 6 | `geo3d_core` | datapath: MACs, saturação, projeção, divisor, outcodes, estados |
| 7-13 | `vdp` (V9968) e submódulos | blocos por módulo, gerados da hierarquia Verilog |

O desenho fica no nível de portas onde isso ainda é legível. O design inteiro em portas teria cerca de 11 mil LUTs.
Os números de recursos vêm da build Gowin `v2_locr2` (geo3d v2.0, 2026-10-13), copiados em `gen/data/`.

## Regenerar

```powershell
geo3d\docs\kicad\gen\export_pdf.ps1
```

O script roda `gen/gen.py` com o Python do próprio KiCad e exporta o PDF com o `kicad-cli`.
O gerador lê o RTL de `fpga/V9968_Cartridge_TangNano20K_geo3d/src` (topo e V9968, de HRA!) e de `geo3d/rtl` (geo3d v2.0).

As páginas do V9968 acompanham o RTL automaticamente.
As de `msx_slot`, `geo3d_bus`, `geo3d_engine` e `geo3d_core` foram desenhadas à mão no gerador (`p_gate.py`, `p_rtl.py`) e precisam ser revistas quando esses módulos mudarem.

Arquivos do gerador:

- `vparse.py`: lê os módulos, as portas e as instâncias do Verilog;
- `ksch.py`: escreve o formato `.kicad_sch` (portas lógicas, blocos, folhas, rótulos, junções);
- `p_*.py`: uma página ou um grupo de páginas cada;
- `common.py`: recursos da síntese e helpers de layout.
