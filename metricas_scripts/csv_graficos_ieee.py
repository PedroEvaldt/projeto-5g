#!/usr/bin/env python3
"""
Gera as duas figuras do artigo (Smart-City e Hierarchical Slicing) a partir
dos CSVs brutos por execução (qoe_timeseries.csv, auction_history.csv,
test_events.csv), agregando as 30 execuções por período de 30s.

Este arquivo é standalone: NÃO importa nem altera csv_planilhas.py.
A função `agregar_dados_cenario` replica a mesma lógica de agregação já
validada (mesma usada no csv_graficos_ieee.py), só a parte de PLOTAGEM foi
redesenhada.

Principal problema corrigido em relação à versão anterior (Gemini):
    - Os rótulos de evento (texto completo, rotacionado 40°) se
      sobrepunham e ficavam ilegíveis quando os eventos estavam
      próximos no tempo (ver PDFs enviados).
    - Solução: cada evento vira apenas uma linha vertical fina + um
      marcador circular numerado (①②③...) no topo do primeiro subplot.
      O texto completo de cada evento vai na legenda do próprio gráfico
      (fora da área de plotagem) e/ou na legenda (\\caption) do LaTeX,
      nunca sobreposto aos dados.

Uso (no seu ambiente, com os dados reais):
    python3 gerar_figuras_ieee.py
Requer os diretórios `resultados/cidade_inteligente_3` e
`resultados/slices_hierarquicos` (ajuste DIR_CIDADE / DIR_HIER abaixo).

VR (Fase 4): com --vr <pasta de uma configuração>, gera só a figura do cenário com VR (hvr_timeline.pdf e
_1col): banda e CPU dadas pelo leilão (com os pisos da VR do vr/calibracao.yaml tracejados), bitrate dos
vídeos e % de janelas da VR fora do SLA, por período de 30 s, média ± desvio entre as execuções da pasta.
A linha da VR na banda e na CPU aparece quando a VR estiver no leilão (Fase 5). Execuções com VR ficam
numa pasta por configuração, fora das pastas dos cenários só de vídeo, para não entrarem nas figuras deles.
    python3 csv_graficos_ieee.py --vr resultados/fase4_vr/fase4_exec02
"""

import argparse
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
import glob
import os
import yaml

# ==========================================
# CONFIGURAÇÕES GLOBAIS - PADRÃO IEEE
# ==========================================
plt.rcParams.update({
    'font.family': 'serif',
    'font.serif': ['Times New Roman', 'DejaVu Serif'],
    'font.size': 9,
    'axes.labelsize': 9,
    'axes.titlesize': 9,
    'legend.fontsize': 8,
    'xtick.labelsize': 8,
    'ytick.labelsize': 8,
    'lines.linewidth': 1.6,
    'axes.grid': True,
    'grid.alpha': 0.25,
    'grid.linestyle': '--',
    'axes.spines.top': False,
    'axes.spines.right': False,
})

CORES = {
    'ambulancia': '#d62728', 'ambulance': '#d62728',
    'bombeiros': '#ff7f0e', 'firefighters': '#ff7f0e',
    'policia': '#1f77b4', 'police': '#1f77b4',
    'gold': '#b8860b',      # dourado escurecido (o amarelo puro some no fundo branco)
    'silver': '#555555',    # cinza escuro (o cinza claro original sumia)
    'bronze': '#8c510a',
    'vr': '#6a3d9a',        # roxo: longe dos tons de ouro/cinza/bronze do vídeo
}
# nomes de exibição (Português no CSV -> rótulo em inglês para o artigo)
DISPLAY = {
    'ambulancia': 'Ambulance', 'bombeiros': 'Firefighters', 'policia': 'Police',
    'gold': 'Gold', 'silver': 'Silver', 'bronze': 'Bronze', 'vr': 'VR',
}

DURACAO_TESTE_NOMINAL = 900
CALIBRACAO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vr", "calibracao.yaml")

# ==========================================
# 1. AGREGAÇÃO (mesma lógica do csv_graficos_ieee.py, para rodar nos seus CSVs brutos)
# ==========================================
def agregar_dados_cenario(base_dir):
    print(f"Agregando dados de: {base_dir}")

    arq_qoe = glob.glob(os.path.join(base_dir, "**", "qoe_timeseries.csv"), recursive=True)
    df_qoe = pd.concat([pd.read_csv(f) for f in arq_qoe], ignore_index=True)
    df_qoe['Buffer_Seconds'] = pd.to_numeric(df_qoe['Buffer_Seconds'], errors='coerce')
    df_qoe['Timestamp_Offset_Seconds'] = pd.to_numeric(df_qoe['Timestamp_Offset_Seconds'], errors='coerce')
    df_qoe['Bitrate_kbps'] = pd.to_numeric(df_qoe['Bitrate_kbps'], errors='coerce')
    df_qoe = df_qoe[df_qoe['Timestamp_Offset_Seconds'] <= 950]
    is_stalled = (df_qoe['Buffer_Seconds'] <= 0.5) & (df_qoe['Timestamp_Offset_Seconds'] > 10.0)
    df_qoe.loc[is_stalled, 'Bitrate_kbps'] = 0
    if 'Status' in df_qoe.columns:
        df_qoe.loc[df_qoe['Status'] != 'OK', 'Bitrate_kbps'] = 0
    df_qoe['Period'] = (df_qoe['Timestamp_Offset_Seconds'] // 30).astype(int) * 30
    df_qoe = df_qoe[df_qoe['Period'] <= DURACAO_TESTE_NOMINAL]
    df_qoe_agg = df_qoe.groupby(['Period', 'Slice'])['Bitrate_kbps'].agg(['mean', 'std']).reset_index()
    df_final = df_qoe_agg.pivot(index='Period', columns='Slice', values='mean').add_suffix('_Bitrate')
    df_final = df_final.join(
        df_qoe_agg.pivot(index='Period', columns='Slice', values='std').add_suffix('_Bitrate_std'))

    arq_auc = glob.glob(os.path.join(base_dir, "**", "auction_history.csv"), recursive=True)
    if arq_auc:
        lista = []
        for f in arq_auc:
            df_a = pd.read_csv(f)
            df_a['Timestamp'] = pd.to_datetime(df_a['Timestamp'])
            df_a['Time_Diff'] = df_a['Timestamp'].diff().dt.total_seconds()
            df_a['Internal_Run_ID'] = (df_a['Time_Diff'] > 120).cumsum()
            df_a['Offset'] = df_a.groupby('Internal_Run_ID')['Timestamp'].transform(
                lambda x: (x - x.min()).dt.total_seconds())
            df_a = df_a[df_a['Offset'] <= 950]
            lista.append(df_a)
        df_auc = pd.concat(lista, ignore_index=True)
        df_auc['Slice'] = df_auc['Agent'].str.replace('_slice', '')
        df_auc['Auction_ID_Norm'] = df_auc.groupby('Internal_Run_ID')['Auction_ID'].transform(lambda x: x - x.min())
        df_auc['Period'] = df_auc['Auction_ID_Norm'] * 30
        df_auc = df_auc[df_auc['Period'] <= DURACAO_TESTE_NOMINAL]
        # A CPU entrou na Fase 4 (figura da VR); as figuras do vídeo não a usam
        metricas = ['Bid_Value', 'BW_Allocated', 'CPU_Allocated']
        df_auc_mean = df_auc.groupby(['Period', 'Slice'])[metricas].mean().reset_index()
        df_auc_std = df_auc.groupby(['Period', 'Slice'])[metricas].std().reset_index()
        pivot_bid = df_auc_mean.pivot(index='Period', columns='Slice', values='Bid_Value').add_suffix('_Bid')
        pivot_bw = df_auc_mean.pivot(index='Period', columns='Slice', values='BW_Allocated').add_suffix('_BW')
        pivot_cpu = df_auc_mean.pivot(index='Period', columns='Slice', values='CPU_Allocated').add_suffix('_CPU')
        pivot_bid_std = df_auc_std.pivot(index='Period', columns='Slice', values='Bid_Value').add_suffix('_Bid_std')
        pivot_bw_std = df_auc_std.pivot(index='Period', columns='Slice', values='BW_Allocated').add_suffix('_BW_std')
        pivot_cpu_std = df_auc_std.pivot(index='Period', columns='Slice', values='CPU_Allocated').add_suffix('_CPU_std')
        for piv in (pivot_bid, pivot_bw, pivot_bid_std, pivot_bw_std, pivot_cpu, pivot_cpu_std):
            df_final = pd.merge(df_final, piv, on='Period', how='outer')

    df_final = df_final.sort_values('Period').reset_index()
    df_final = df_final.ffill().fillna(0)
    return df_final


def agregar_vr(base_dir):
    """% de janelas da VR fora do SLA e net p95 mediano por período de 30 s: primeiro dentro de cada execução,
    depois média e desvio entre as execuções. Período sem janela (VR fora) fica vazio, e vira um buraco na linha.
    Devolve (tabela, número de execuções), ou (None, 0) se a pasta não tiver VR."""
    arqs = glob.glob(os.path.join(base_dir, "**", "qoe_vr_timeseries.csv"), recursive=True)
    por_execucao = []
    for f in arqs:
        d = pd.read_csv(f)
        d = d[~d['Window_Type'].isin(['pausa', 'retomada']) & (d['Health'] == 1)]   # health=0: falha do instrumento
        d = d[d['Timestamp_Offset_Seconds'] <= 950]
        d['Period'] = (d['Timestamp_Offset_Seconds'] // 30).astype(int) * 30
        d = d[d['Period'] <= DURACAO_TESTE_NOMINAL]
        por_execucao.append(d.groupby('Period').agg(
            fora=('SLA_OK', lambda s: 100 * (1 - s.mean())), net=('Net_p95_ms', 'median')).reset_index())
    if not por_execucao:
        return None, 0
    todas = pd.concat(por_execucao, ignore_index=True)
    vr = todas.groupby('Period').agg(vr_OutSLA=('fora', 'mean'), vr_OutSLA_std=('fora', 'std'),
                                     vr_Net=('net', 'mean')).reset_index()
    periodos = pd.DataFrame({'Period': range(0, DURACAO_TESTE_NOMINAL + 1, 30)})
    return periodos.merge(vr, on='Period', how='left'), len(arqs)


def ler_pisos():
    """Pisos da VR (banda em Mbps e CPU em núcleos) do vr/calibracao.yaml."""
    with open(CALIBRACAO) as f:
        piso = yaml.safe_load(f)['piso']
    return {'BW': piso['banda_mbps'], 'CPU': piso['cpu']}


def carregar_eventos(base_dir):
    """Le test_events.csv de uma execucao representativa e retorna
    [(period, description), ...] ja arredondado pro bucket de 30s.

    Ignora:
      - marcadores genericos de inicio/fim de teste (TEST_START/TEST_END);
      - linhas "_SENT" (ex.: PRIORITY_CHANGE_SENT), que sao apenas a
        confirmacao de entrega via XMPP do mesmo evento logo acima e
        duplicavam cada intent como dois marcadores no grafico."""
    arq_evt = glob.glob(os.path.join(base_dir, "**", "test_events.csv"), recursive=True)
    if not arq_evt:
        return []
    df_evt = pd.read_csv(arq_evt[0])
    if 'Event_Type' in df_evt.columns:
        df_evt = df_evt[~df_evt['Event_Type'].astype(str).str.contains('SENT', case=False, na=False)]
        df_evt = df_evt[~df_evt['Event_Type'].astype(str).isin(['TEST_START', 'TEST_END'])]
    df_evt = df_evt[~df_evt['Description'].str.contains(
        'Teste inici|Teste final|entregue via XMPP', na=False, regex=True)]
    eventos = []
    for _, row in df_evt.iterrows():
        desc = str(row['Description']).split('|')[0].strip()
        eventos.append((float(row['Timestamp_Offset_Seconds']), desc))
    return eventos


# ==========================================
# 2. MARCAÇÃO DE EVENTOS (numerada, sem sobreposição)
# ==========================================
_TAGS = [str(i) for i in range(1, 9)]  # numeros simples: DejaVu Serif nao tem os glifos circulados


def marcar_eventos(axes, eventos):
    """Desenha uma linha vertical fina em todos os subplots e um marcador
    circular numerado apenas no topo do primeiro subplot. Retorna a lista
    (tag, descricao) para usar na legenda/caption, em vez de escrever o
    texto em cima do gráfico (o que causava a sobreposição ilegível)."""
    legenda = []
    for i, (periodo, desc) in enumerate(eventos):
        tag = _TAGS[i] if i < len(_TAGS) else f"({i+1})"
        for ax in axes:
            ax.axvline(x=periodo, color='#999999', linestyle=':', linewidth=1.0, zorder=1)
        axes[0].annotate(
            tag, xy=(periodo, 1.0), xycoords=('data', 'axes fraction'),
            xytext=(0, 3), textcoords='offset points',
            ha='center', va='bottom', fontsize=7.5,
            bbox=dict(boxstyle='circle,pad=0.18', fc='white', ec='#444444', lw=0.7),
            annotation_clip=False,
        )
        legenda.append((tag, f"t={periodo:.0f}s: {desc}"))
    return legenda


def legenda_de_series(ax, colunas, sufixo):
    handles, labels = [], []
    for col in colunas:
        nome_slice = col[: -len(f"_{sufixo}")].lower()
        h, = ax.plot([], [], color=CORES.get(nome_slice, '#333333'), label=DISPLAY.get(nome_slice, nome_slice))
        handles.append(h)
        labels.append(DISPLAY.get(nome_slice, nome_slice))
    return handles, labels


# ==========================================
# 3. FIGURAS
# ==========================================
def plot_smart_city(df, eventos, out_path='smartcity_timeline.pdf', coluna_unica=False):
    """coluna_unica=True gera a figura no tamanho de UMA coluna do IEEEtran
    (~3.45in) em vez de duas (7.16in), usando 'figure' em vez de 'figure*'
    no LaTeX. Os 3 paineis (Bid, BW, Bitrate) sao mantidos nos dois casos --
    só a largura muda; a figura fica mais alta e a fonte um pouco menor
    pra compensar."""
    specs = [('Bid', 'Bid value'), ('BW', 'Allocated BW\n(Mbps)'), ('Bitrate', 'Bitrate\n(kbps)')]
    if coluna_unica:
        fig, axes = plt.subplots(3, 1, figsize=(3.45, 5.6), sharex=True,
                                  gridspec_kw={'hspace': 0.14})
        leg_y = 1.24
        _fontctx = {'font.size': 7.5, 'axes.labelsize': 7.5, 'legend.fontsize': 7,
                    'xtick.labelsize': 6.5, 'ytick.labelsize': 6.5}
    else:
        fig, axes = plt.subplots(3, 1, figsize=(7.16, 5.4), sharex=True,
                                  gridspec_kw={'hspace': 0.12})
        leg_y = 1.18
        _fontctx = {}

    with plt.rc_context(_fontctx):
        handles = labels = None
        for sufixo, ylabel, ax in [(*s, axes[i]) for i, s in enumerate(specs)]:
            cols = sorted([c for c in df.columns if c.endswith(f'_{sufixo}') and not c.endswith(f'_{sufixo}_std')])
            for col in cols:
                nome_slice = col[: -len(f"_{sufixo}")].lower()
                cor = CORES.get(nome_slice, '#333333')
                ax.plot(df['Period'], df[col], color=cor, linewidth=1.3 if coluna_unica else 1.6, zorder=3)
                std_col = f"{nome_slice}_{sufixo}_std"
                if std_col in df.columns:
                    lo = df[col] - df[std_col]
                    hi = df[col] + df[std_col]
                    if sufixo in ('BW',):  # BW e outras metricas nao-negativas
                        lo = lo.clip(lower=0)
                    ax.fill_between(df['Period'], lo, hi, color=cor, alpha=0.15, linewidth=0, zorder=2)
            if sufixo == specs[0][0]:
                handles, labels = legenda_de_series(ax, cols, sufixo)
            ax.set_ylabel(ylabel)
            ax.margins(x=0.01)

        legenda_eventos = marcar_eventos(axes, eventos)

        axes[0].legend(handles, labels, loc='lower center', bbox_to_anchor=(0.5, leg_y),
                       ncol=len(labels), frameon=False, columnspacing=1.0 if coluna_unica else 1.2,
                       handlelength=1.3 if coluna_unica else 1.6)

        axes[-1].set_xlabel('Time (s)')
        axes[-1].set_xlim(0, DURACAO_TESTE_NOMINAL)

        fig.savefig(out_path, format='pdf', bbox_inches='tight')
    print(f"[OK] Salvo: {out_path}")
    for tag, txt in legenda_eventos:
        print(f"      {tag} {txt}")
    return legenda_eventos


def plot_hierarchical(df, eventos, out_path='hierarchical_timeline.pdf', coluna_unica=False):
    if coluna_unica:
        fig, axes = plt.subplots(2, 1, figsize=(3.45, 3.15), sharex=True,
                                  gridspec_kw={'hspace': 0.14})
        leg_y = 1.30
        _fontctx = {'font.size': 7.5, 'axes.labelsize': 7.5, 'legend.fontsize': 7,
                    'xtick.labelsize': 6.5, 'ytick.labelsize': 6.5}
    else:
        fig, axes = plt.subplots(2, 1, figsize=(7.16, 3.9), sharex=True,
                                  gridspec_kw={'hspace': 0.12})
        leg_y = 1.22
        _fontctx = {}

    specs = [('BW', 'BW (Mbps)' if coluna_unica else 'Allocated BW\n(Mbps)'),
             ('Bitrate', 'Bitrate\n(kbps)')]

    with plt.rc_context(_fontctx):
        handles = labels = None
        for sufixo, ylabel, ax in [(*s, axes[i]) for i, s in enumerate(specs)]:
            cols = sorted([c for c in df.columns if c.endswith(f'_{sufixo}') and not c.endswith(f'_{sufixo}_std')])
            for col in cols:
                nome_slice = col[: -len(f"_{sufixo}")].lower()
                cor = CORES.get(nome_slice, '#333333')
                ax.plot(df['Period'], df[col], color=cor, linewidth=1.3 if coluna_unica else 1.6, zorder=3)
                std_col = f"{nome_slice}_{sufixo}_std"
                if std_col in df.columns:
                    lo = (df[col] - df[std_col]).clip(lower=0)
                    hi = df[col] + df[std_col]
                    ax.fill_between(df['Period'], lo, hi, color=cor, alpha=0.15, linewidth=0, zorder=2)
            if sufixo == 'BW':
                handles, labels = legenda_de_series(ax, cols, sufixo)
            ax.set_ylabel(ylabel)
            ax.margins(x=0.01)

        legenda_eventos = marcar_eventos(axes, eventos)

        axes[0].legend(handles, labels, loc='lower center', bbox_to_anchor=(0.5, leg_y),
                       ncol=len(labels), frameon=False, columnspacing=1.0 if coluna_unica else 1.2,
                       handlelength=1.3 if coluna_unica else 1.6)

        axes[-1].set_xlabel('Time (s)')
        axes[-1].set_xlim(0, DURACAO_TESTE_NOMINAL)

        fig.savefig(out_path, format='pdf', bbox_inches='tight')
    print(f"[OK] Salvo: {out_path}")
    for tag, txt in legenda_eventos:
        print(f"      {tag} {txt}")
    return legenda_eventos


def plot_hvr(df, vr, eventos, out_path='hvr_timeline.pdf', coluna_unica=False):
    """Cenário com VR: banda e CPU dadas pelo leilão (com os pisos da VR tracejados), bitrate dos vídeos e
    % de janelas da VR fora do SLA. Mesmo período de 30 s e mesma marcação de eventos das outras figuras."""
    if coluna_unica:
        fig, axes = plt.subplots(4, 1, figsize=(3.45, 6.0), sharex=True,
                                  gridspec_kw={'hspace': 0.14})
        leg_y = 1.30
        _fontctx = {'font.size': 7.5, 'axes.labelsize': 7.5, 'legend.fontsize': 7,
                    'xtick.labelsize': 6.5, 'ytick.labelsize': 6.5}
    else:
        fig, axes = plt.subplots(4, 1, figsize=(7.16, 6.6), sharex=True,
                                  gridspec_kw={'hspace': 0.12})
        leg_y = 1.22
        _fontctx = {}

    specs = [('BW', 'BW (Mbps)' if coluna_unica else 'Allocated BW\n(Mbps)'),
             ('CPU', 'CPU (cores)' if coluna_unica else 'Allocated CPU\n(cores)'),
             ('Bitrate', 'Bitrate\n(kbps)')]
    pisos = ler_pisos()
    largura = 1.3 if coluna_unica else 1.6

    with plt.rc_context(_fontctx):
        handles = labels = None
        for (sufixo, ylabel), ax in zip(specs, axes):
            cols = sorted([c for c in df.columns if c.endswith(f'_{sufixo}') and not c.endswith(f'_{sufixo}_std')])
            for col in cols:
                nome_slice = col[: -len(f"_{sufixo}")].lower()
                cor = CORES.get(nome_slice, '#333333')
                ax.plot(df['Period'], df[col], color=cor, linewidth=largura, zorder=3)
                std_col = f"{nome_slice}_{sufixo}_std"
                if std_col in df.columns:
                    lo = (df[col] - df[std_col]).clip(lower=0)
                    hi = df[col] + df[std_col]
                    ax.fill_between(df['Period'], lo, hi, color=cor, alpha=0.15, linewidth=0, zorder=2)
            if sufixo in pisos:                                  # o piso da VR, mesmo antes de ela entrar no leilão
                ax.axhline(pisos[sufixo], color=CORES['vr'], linestyle='--', linewidth=0.9, zorder=2)
                ax.annotate('VR floor', xy=(1, pisos[sufixo]), xycoords=('axes fraction', 'data'),
                            xytext=(0, 2), textcoords='offset points', ha='right', va='bottom',
                            fontsize=6.5, color=CORES['vr'])
            if sufixo == 'BW':
                handles, labels = legenda_de_series(ax, cols, sufixo)
            ax.set_ylabel(ylabel)
            ax.margins(x=0.01)

        # % de janelas da VR fora do SLA: média entre as execuções, faixa = ± desvio (0 com uma execução só)
        ax = axes[3]
        ax.plot(vr['Period'], vr['vr_OutSLA'], color=CORES['vr'], linewidth=largura, marker='o', ms=2.5, zorder=3)
        desvio = vr['vr_OutSLA_std'].fillna(0)
        ax.fill_between(vr['Period'], (vr['vr_OutSLA'] - desvio).clip(lower=0), (vr['vr_OutSLA'] + desvio).clip(upper=100),
                        color=CORES['vr'], alpha=0.15, linewidth=0, zorder=2)
        ax.set_ylim(-3, 103)
        ax.set_ylabel('VR windows\nout of SLA (%)')
        ax.margins(x=0.01)
        if 'VR' not in labels:                                   # a VR fora do leilão (antes da Fase 5) também vai na legenda
            h, = axes[0].plot([], [], color=CORES['vr'])
            handles, labels = handles + [h], labels + ['VR']

        legenda_eventos = marcar_eventos(axes, eventos)

        axes[0].legend(handles, labels, loc='lower center', bbox_to_anchor=(0.5, leg_y),
                       ncol=len(labels), frameon=False, columnspacing=1.0 if coluna_unica else 1.2,
                       handlelength=1.3 if coluna_unica else 1.6)

        axes[-1].set_xlabel('Time (s)')
        axes[-1].set_xlim(0, DURACAO_TESTE_NOMINAL)

        fig.savefig(out_path, format='pdf', bbox_inches='tight')
    plt.close(fig)
    print(f"[OK] Salvo: {out_path}")
    for tag, txt in legenda_eventos:
        print(f"      {tag} {txt}")
    return legenda_eventos


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Figuras do artigo (sem opções: as dos cenários de vídeo)")
    ap.add_argument("--vr", metavar="PASTA", help="pasta de uma configuração com VR: gera só o hvr_timeline")
    args = ap.parse_args()

    if args.vr:
        vr, n = agregar_vr(args.vr)
        if vr is None:
            raise SystemExit(f"[ERRO] Nenhum qoe_vr_timeseries.csv em {args.vr} (rode o analise_metricas_vr.py nas execuções)")
        print(f"[VR] {n} execução(ões) em {args.vr}")
        df_vr = agregar_dados_cenario(args.vr)
        eventos_vr = carregar_eventos(args.vr)
        plot_hvr(df_vr, vr, eventos_vr, out_path='hvr_timeline.pdf', coluna_unica=False)
        plot_hvr(df_vr, vr, eventos_vr, out_path='hvr_timeline_1col.pdf', coluna_unica=True)
    else:
        DIR_CIDADE = "resultados/cidade_inteligente_3"
        DIR_HIER = "resultados/slices_hierarquicos"

        if os.path.exists(DIR_CIDADE):
            df_cid = agregar_dados_cenario(DIR_CIDADE)
            eventos_cid = carregar_eventos(DIR_CIDADE)
            plot_smart_city(df_cid, eventos_cid, out_path='smartcity_timeline.pdf', coluna_unica=False)
            plot_smart_city(df_cid, eventos_cid, out_path='smartcity_timeline_1col.pdf', coluna_unica=True)
        else:
            print(f"[AVISO] Diretório não encontrado: {DIR_CIDADE}")

        if os.path.exists(DIR_HIER):
            df_hier = agregar_dados_cenario(DIR_HIER)
            eventos_hier = carregar_eventos(DIR_HIER)
            plot_hierarchical(df_hier, eventos_hier, out_path='hierarchical_timeline.pdf', coluna_unica=False)
            plot_hierarchical(df_hier, eventos_hier, out_path='hierarchical_timeline_1col.pdf', coluna_unica=True)
        else:
            print(f"[AVISO] Diretório não encontrado: {DIR_HIER}")