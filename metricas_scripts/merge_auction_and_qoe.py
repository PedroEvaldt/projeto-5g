#!/usr/bin/env python3
"""
Auction and QoE Data Correlation
=====================================
Merges auction_history.csv, test_events.csv, and qoe_timeseries.csv
to create a unified test analysis and generate graphs.

If the run also has qoe_vr_timeseries.csv (from analise_metricas_vr.py), adds the VR:
correlation_vr.csv (per 30 s period: % of windows out of SLA, net p95, continuity and the
CPU/BW the auction gave the VR slice) and graph_vr_events.png. The video outputs do not change.
"""
import math
import os
import re
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import matplotlib.transforms as mtransforms
import seaborn as sns
import sys
import warnings
import yaml
from pathlib import Path

# SLA and floors of the VR (Phase 3)
CALIBRACAO = Path(__file__).resolve().parent.parent / "vr" / "calibracao.yaml"
VR_MARKS = ['pausa', 'retomada']

# Suppress seaborn visual warnings in the terminal
warnings.filterwarnings('ignore')

class DataCorrelator:
    def __init__(self, input_dir = "resultados/", output_dir="resultados/teste/"):
        self.output_dir = output_dir
        self.input_dir = input_dir
        self.auction_file = f"{input_dir}auction_history.csv"
        self.events_file = f"{input_dir}test_events.csv"
        self.timeseries_file = f"{input_dir}qoe_timeseries.csv"
        self.correlation_file = f"{output_dir}correlation_analysis.csv"
        self.vr_file = f"{input_dir}qoe_vr_timeseries.csv"
        self.vr_correlation_file = f"{output_dir}correlation_vr.csv"

    def load_data(self):
        """Loads the three CSV files."""
        try:
            auction_df = pd.read_csv(self.auction_file)
            print(f"[OK] Loaded auction_history: {len(auction_df)} rows")
        except FileNotFoundError:
            print(f"[WARN] {self.auction_file} not found")
            auction_df = None
        
        try:
            events_df = pd.read_csv(self.events_file)
            print(f"[OK] Loaded test_events: {len(events_df)} rows")
        except FileNotFoundError:
            print(f"[WARN] {self.events_file} not found")
            events_df = None
        
        try:
            timeseries_df = pd.read_csv(self.timeseries_file)
            print(f"[OK] Loaded qoe_timeseries: {len(timeseries_df)} rows")
        except FileNotFoundError:
            print(f"[WARN] {self.timeseries_file} not found")
            timeseries_df = None
        
        return auction_df, events_df, timeseries_df
    
    def analyze_qoe_by_slice(self, timeseries_df):
        """Calculates QoE statistics per slice."""
        if timeseries_df is None:
            return None
        
        # Filter the DataFrame to keep only rows with Status 'OK'.
        if 'Status' in timeseries_df.columns:
            df_analise = timeseries_df[timeseries_df['Status'] == 'OK'].copy()
        else:
            df_analise = timeseries_df.copy()
        
        df_analise['Timestamp_Offset_Seconds'] = pd.to_numeric(df_analise['Timestamp_Offset_Seconds'], errors='coerce')
        df_analise['Bitrate_kbps'] = pd.to_numeric(df_analise['Bitrate_kbps'], errors='coerce')
        df_analise['Buffer_Seconds'] = pd.to_numeric(df_analise['Buffer_Seconds'], errors='coerce')

        # If there is a stall, zero out the bitrate so averages are not skewed
        is_stalled = (df_analise['Buffer_Seconds'] <= 0.5) & (df_analise['Timestamp_Offset_Seconds'] > 10.0)
        df_analise.loc[is_stalled, 'Bitrate_kbps'] = 0
        
        stats = df_analise.groupby('Slice').agg({
            'Bitrate_kbps': ['mean', 'std', 'min', 'max'],
            'Buffer_Seconds': ['mean', 'std', 'min', 'max'],
            'Quality_Switches': 'max',
            'Stalls': 'max'
        }).round(2)
        
        return stats
    
    def analyze_auction_outcomes(self, auction_df):
        """Analyzes auction outcomes per slice."""
        if auction_df is None:
            return None
        
        auction_df['Bid_Value'] = pd.to_numeric(auction_df['Bid_Value'], errors='coerce')
        auction_df['BW_Allocated'] = pd.to_numeric(auction_df['BW_Allocated'], errors='coerce')
        
        stats = auction_df.groupby('Agent').agg({
            'Result': lambda x: (x == 'WINNER').sum(),
            'Bid_Value': ['mean', 'min', 'max'],
            'BW_Allocated': ['mean', 'min', 'max']
        }).round(2)
        
        stats.columns = ['Victories', 'Avg_Bid', 'Min_Bid', 'Max_Bid', 'Avg_BW', 'Min_BW', 'Max_BW']
        return stats
    
    def create_correlation_timeline(self, auction_df, events_df, timeseries_df):
        """Creates a timeline correlating events, auctions, and QoE."""
        if timeseries_df is None:
            return None
        
        timeseries_df['Timestamp_Offset_Seconds'] = pd.to_numeric(timeseries_df['Timestamp_Offset_Seconds'], errors='coerce')
        timeseries_df['Bitrate_kbps'] = pd.to_numeric(timeseries_df['Bitrate_kbps'], errors='coerce')
        timeseries_df['Buffer_Seconds'] = pd.to_numeric(timeseries_df['Buffer_Seconds'], errors='coerce')
        
        timeseries_df['Period'] = (timeseries_df['Timestamp_Offset_Seconds'] // 30).astype(int) * 30
        
        period_stats = timeseries_df.groupby(['Period', 'Slice']).agg({
            'Bitrate_kbps': 'mean',
            'Buffer_Seconds': 'mean',
            'Quality_Switches': 'max',
            'Stalls': 'max'
        }).reset_index()
        
        if events_df is not None:
            events_df['Timestamp_Offset_Seconds'] = pd.to_numeric(events_df['Timestamp_Offset_Seconds'], errors='coerce')
            period_stats = period_stats.merge(
                events_df[['Timestamp_Offset_Seconds', 'Event_Type', 'Description']],
                left_on='Period',
                right_on='Timestamp_Offset_Seconds',
                how='left'
            )
        
        return period_stats

    def generate_graphs(self, auction_df, events_df, timeseries_df):
        """Generates graphs correlating events with auctions and QoE metrics."""
        if auction_df is None or events_df is None or timeseries_df is None:
            print("[WARN] Insufficient data to generate graphs.")
            return

        print("\n[INIT] Generating visualization graphs...")
        sns.set_theme(style="whitegrid")
        colors = {'PRIORITY_CHANGE': 'red', 'TRAFFIC_PAUSE': 'purple', 'TRAFFIC_RESUME': 'green', 'TEST_START': 'black', 'TEST_END': 'black'}

        # --- 1. AUCTION VS EVENTS GRAPH ---
        # Since the auction has full datetimes, we need to calculate the offset in seconds
        if 'Timestamp_Offset_Seconds' not in auction_df.columns:
            auction_df['Timestamp'] = pd.to_datetime(auction_df['Timestamp'])
            t0_auction = auction_df['Timestamp'].min()
            auction_df['Timestamp_Offset_Seconds'] = (auction_df['Timestamp'] - t0_auction).dt.total_seconds()
        
        fig, axes = plt.subplots(2, 1, figsize=(14, 10), sharex=True)
        
        sns.lineplot(data=auction_df, x='Timestamp_Offset_Seconds', y='Bid_Value', hue='Agent', marker='o', ax=axes[0])
        axes[0].set_title('Bid Value Over Time by Slice', fontsize=14)
        axes[0].set_ylabel('Bid Value')
        
        sns.lineplot(data=auction_df, x='Timestamp_Offset_Seconds', y='BW_Allocated', hue='Agent', marker='s', ax=axes[1])
        axes[1].set_title('Allocated Bandwidth (BW) Over Time by Slice', fontsize=14)
        axes[1].set_ylabel('BW Allocated')
        axes[1].set_xlabel('Test Time (Seconds)')
        
        # Draw vertical lines for events
        for _, row in events_df.iterrows():
            t = row['Timestamp_Offset_Seconds']
            e_type = row['Event_Type']
            if pd.isna(t): continue
            c = colors.get(e_type, 'gray')
            axes[0].axvline(t, color=c, linestyle='--', alpha=0.7)
            axes[1].axvline(t, color=c, linestyle='--', alpha=0.7)
            axes[0].text(t + 2, axes[0].get_ylim()[1] * 0.9, e_type, rotation=90, color=c, fontsize=8, verticalalignment='top')
            
        plt.tight_layout()
        auction_plot_path = f"{self.output_dir}graph_auction_events.png"
        plt.savefig(auction_plot_path)
        plt.close()
        print(f"[OK] Auction graph saved to: {auction_plot_path}")

        # --- 2. QOE VS EVENTS GRAPH ---
        qoe_df = timeseries_df.copy()
        qoe_df['Timestamp_Offset_Seconds'] = pd.to_numeric(qoe_df['Timestamp_Offset_Seconds'], errors='coerce')
        qoe_df['Bitrate_kbps'] = pd.to_numeric(qoe_df['Bitrate_kbps'], errors='coerce')
        qoe_df['Buffer_Seconds'] = pd.to_numeric(qoe_df['Buffer_Seconds'], errors='coerce')
        
        # Stall filter to visually reflect drops to zero in the network
        is_stalled = (qoe_df['Buffer_Seconds'] <= 0.5) & (qoe_df['Timestamp_Offset_Seconds'] > 10.0)
        qoe_df.loc[is_stalled, 'Bitrate_kbps'] = 0
        
        fig, axes = plt.subplots(2, 1, figsize=(14, 10), sharex=True)
        
        sns.lineplot(data=qoe_df, x='Timestamp_Offset_Seconds', y='Bitrate_kbps', hue='Slice', ax=axes[0])
        axes[0].set_title('Video Quality (Bitrate) Over Time by Slice', fontsize=14)
        axes[0].set_ylabel('Bitrate (kbps)')
        
        sns.lineplot(data=qoe_df, x='Timestamp_Offset_Seconds', y='Buffer_Seconds', hue='Slice', ax=axes[1])
        axes[1].set_title('Playback Health (Buffer) Over Time by Slice', fontsize=14)
        axes[1].set_ylabel('Buffer (Seconds)')
        axes[1].set_xlabel('Test Time (Seconds)')
        
        for _, row in events_df.iterrows():
            t = row['Timestamp_Offset_Seconds']
            e_type = row['Event_Type']
            if pd.isna(t): continue
            c = colors.get(e_type, 'gray')
            axes[0].axvline(t, color=c, linestyle='--', alpha=0.7)
            axes[1].axvline(t, color=c, linestyle='--', alpha=0.7)
            axes[0].text(t + 2, axes[0].get_ylim()[1] * 0.9, e_type, rotation=90, color=c, fontsize=8, verticalalignment='top')
            
        plt.tight_layout()
        qoe_plot_path = f"{self.output_dir}graph_qoe_events.png"
        plt.savefig(qoe_plot_path)
        plt.close()
        print(f"[OK] QoE graph saved to: {qoe_plot_path}")

    # --- VR (Phase 4) ---

    def load_vr(self):
        """Loads qoe_vr_timeseries.csv, if the run has it (None otherwise)."""
        if not os.path.exists(self.vr_file):
            return None
        vr_df = pd.read_csv(self.vr_file)
        print(f"[OK] Loaded qoe_vr_timeseries: {len(vr_df)} rows")
        return vr_df

    def test_start(self, events_df):
        """Real start time of the test, written by deploy_test.py in the TEST_START details (None in older runs)."""
        if events_df is None:
            return None
        details = events_df.loc[events_df['Event_Type'] == 'TEST_START', 'Details'].astype(str)
        match = re.search(r"inicio=(\S+)", details.iloc[0]) if len(details) else None
        return pd.Timestamp(match.group(1)) if match else None

    def vr_allocation(self, auction_df, events_df, vr_slice):
        """CPU and BW the auction gave the VR slice, on the events' time axis (None if the VR is not in the auction)."""
        if auction_df is None:
            return None
        alloc = auction_df[auction_df['Agent'] == f"{vr_slice}_slice"].copy()
        if alloc.empty:
            return None
        ts = pd.to_datetime(alloc['Timestamp'])
        start = self.test_start(events_df)
        if start is not None:
            # The auctioneer and deploy_test.py run on the same machine: its naive times are in the start's time zone
            alloc['t'] = (ts.dt.tz_localize(start.tzinfo) - start).dt.total_seconds()
        else:
            # Older runs: from the first auction, like the auction graph (off by some seconds)
            alloc['t'] = (ts - pd.to_datetime(auction_df['Timestamp']).min()).dt.total_seconds()
        for col in ['CPU_Allocated', 'BW_Allocated']:
            alloc[col] = pd.to_numeric(alloc[col], errors='coerce')
        # The auctioneer keeps running until the tmux session is closed: keep only the rounds of the test
        test_end = events_df.loc[events_df['Event_Type'] == 'TEST_END', 'Timestamp_Offset_Seconds'] if events_df is not None else []
        if len(test_end):
            alloc = alloc[alloc['t'] <= float(test_end.iloc[0])]
        return alloc[['t', 'CPU_Allocated', 'BW_Allocated']].sort_values('t')

    def create_vr_timeline(self, vr_df, events_df, alloc):
        """Per 30 s period: VR windows, % out of SLA, net p95, continuity, the VR slice's CPU/BW and the events.
        A window counts in the period where it ends; windows with health=0 (instrument failure) do not count."""
        # Every period that starts before the end of the test, also the ones with the VR off (0 windows)
        end = vr_df['Timestamp_Offset_Seconds'].max()
        if events_df is not None:
            test_end = events_df.loc[events_df['Event_Type'] == 'TEST_END', 'Timestamp_Offset_Seconds']
            end = float(test_end.iloc[0]) if len(test_end) else end
        periods = list(range(0, max(int(-(-end // 1)), 1), 30))

        windows = vr_df[~vr_df['Window_Type'].isin(VR_MARKS) & (vr_df['Health'] == 1)].copy()
        # A window ending exactly at the end of the test goes to the last period
        windows['Period'] = ((windows['Timestamp_Offset_Seconds'] // 30).astype(int) * 30).clip(upper=periods[-1])
        stats = windows.groupby('Period').agg(
            Windows=('SLA_OK', 'size'),
            Out_of_SLA_Pct=('SLA_OK', lambda s: round(100 * (1 - s.mean()), 1)),
            Net_p95_ms_median=('Net_p95_ms', 'median'),
            Continuity_pct_min=('Continuity_pct', 'min'),
        ).reset_index()
        timeline = pd.DataFrame({'Period': periods}).merge(stats, on='Period', how='left')
        timeline['Windows'] = timeline['Windows'].fillna(0).astype(int)

        if alloc is not None:
            # The allocation in force at the end of each period (the last auction round before it)
            timeline['Period_End'] = (timeline['Period'] + 30).astype(float)
            timeline = pd.merge_asof(timeline, alloc.rename(columns={'t': 'Period_End'}), on='Period_End', direction='backward')
            timeline = timeline.drop(columns='Period_End')

        if events_df is not None:
            ev = events_df[~events_df['Event_Type'].astype(str).str.contains('TEST_|SENT', regex=True)].copy()
            ev['Period'] = (pd.to_numeric(ev['Timestamp_Offset_Seconds'], errors='coerce') // 30).astype(int) * 30
            names = ev.groupby('Period')['Event_Type'].agg(' + '.join).rename('Events')
            timeline = timeline.merge(names, on='Period', how='left')
        return timeline

    def generate_vr_graph(self, vr_df, events_df, timeline, alloc):
        """net p95 and continuity per window, % out of SLA per 30 s and, if the VR is in the auction, its CPU and BW."""
        with open(CALIBRACAO) as f:
            calib = yaml.safe_load(f)
        sla, floor = calib['sla'], calib['piso']
        sns.set_theme(style="whitegrid")
        colors = {'PRIORITY_CHANGE': 'red', 'TRAFFIC_PAUSE': 'purple', 'TRAFFIC_RESUME': 'green',
                  'VR_JOIN': 'darkorange', 'VR_LEAVE': 'saddlebrown'}

        windows = vr_df[~vr_df['Window_Type'].isin(VR_MARKS) & (vr_df['Health'] == 1)]
        normal = windows[windows['Window_Type'] == 'normal']
        no_frames = windows.loc[windows['Window_Type'] == 'sem_quadros', 'Timestamp_Offset_Seconds']
        n_panels = 5 if alloc is not None else 3
        fig, axes = plt.subplots(n_panels, 1, figsize=(14, 2.6 * n_panels + 1), sharex=True)

        # 1. net p95 per window (log scale: from ~2 ms to seconds), with the SLA limit; ▼ on top = window without frames
        ax = axes[0]
        ax.scatter(normal['Timestamp_Offset_Seconds'], normal['Net_p95_ms'], s=14, color='tab:blue', zorder=3)
        ax.axhline(sla['net_ref_ms'] + sla['folga_net_ms'], color='gray', linestyle='--', linewidth=1)
        if normal['Net_p95_ms'].notna().any():
            ax.set_yscale('log')
            # 20 instead of 2x10^1, and only 1-2-5 of each decade, so the labels do not pile up
            plain = mticker.FuncFormatter(
                lambda v, _: f"{v:g}" if v > 0 and round(v / 10 ** math.floor(math.log10(v))) in (1, 2, 5) else "")
            ax.yaxis.set_major_formatter(plain)
            ax.yaxis.set_minor_formatter(plain)
        if len(no_frames):
            top = mtransforms.blended_transform_factory(ax.transData, ax.transAxes)
            ax.scatter(no_frames, [0.95] * len(no_frames), marker='v', s=20, color='black', transform=top, zorder=3)
        ax.set_title('VR Network Delay (net p95) per Window', fontsize=14)
        ax.set_ylabel('net p95 (ms)')

        # 2. % of windows out of SLA per 30 s period
        ax = axes[1]
        ax.bar(timeline['Period'] + 15, timeline['Out_of_SLA_Pct'], width=26, color='tab:red', alpha=0.7)
        for p in timeline.loc[timeline['Windows'] == 0, 'Period']:   # gray = no VR windows (VR off), not 0% out of SLA
            ax.axvspan(p, p + 30, color='lightgray', alpha=0.5, linewidth=0)
        ax.set_ylim(0, 105)
        ax.set_title('VR Windows out of SLA per 30 s Period (gray: VR off)', fontsize=14)
        ax.set_ylabel('Out of SLA (%)')

        # 3. continuity per window, with the SLA limit
        ax = axes[2]
        ax.scatter(normal['Timestamp_Offset_Seconds'], normal['Continuity_pct'], s=14, color='tab:blue', zorder=3)
        ax.axhline(sla['cont_min_pct'], color='gray', linestyle='--', linewidth=1)
        ax.set_ylim(top=100.5)
        ax.set_title('VR Continuity per Window', fontsize=14)
        ax.set_ylabel('Continuity (%)')

        # 4-5. CPU and BW the auction gave the VR slice, with its floor
        if alloc is not None:
            for ax, col, key, label in [(axes[3], 'CPU_Allocated', 'cpu', 'CPU (cores)'),
                                        (axes[4], 'BW_Allocated', 'banda_mbps', 'BW (Mbps)')]:
                ax.step(alloc['t'], alloc[col], where='post', color='tab:blue')
                ax.axhline(floor[key], color='gray', linestyle='--', linewidth=1)
                ax.set_title(f'VR Slice {label.split()[0]} Allocated by the Auction (dashed: VR floor)', fontsize=14)
                ax.set_ylabel(label)
        axes[-1].set_xlabel('Test Time (Seconds)')

        if events_df is not None:
            for _, row in events_df.iterrows():
                t, e_type = pd.to_numeric(row['Timestamp_Offset_Seconds'], errors='coerce'), row['Event_Type']
                if pd.isna(t) or e_type in ('TEST_START', 'TEST_END'):
                    continue
                c = colors.get(e_type, 'gray')
                for ax in axes:
                    ax.axvline(t, color=c, linestyle='--', alpha=0.7)
                axes[0].text(t + 2, 0.95, e_type, rotation=90, color=c, fontsize=8, verticalalignment='top',
                             transform=mtransforms.blended_transform_factory(axes[0].transData, axes[0].transAxes))

        plt.tight_layout()
        vr_plot_path = f"{self.output_dir}graph_vr_events.png"
        plt.savefig(vr_plot_path)
        plt.close()
        print(f"[OK] VR graph saved to: {vr_plot_path}")

    def report_vr(self, auction_df, events_df):
        """The VR part of the report: timeline per 30 s and graph (only if the run has qoe_vr_timeseries.csv)."""
        vr_df = self.load_vr()
        if vr_df is None:
            return
        print("\n--- VR: WINDOWS OUT OF SLA PER 30 s PERIOD ---")
        vr_slice = vr_df['Slice'].dropna().iloc[0] if vr_df['Slice'].notna().any() else 'vr'
        alloc = self.vr_allocation(auction_df, events_df, vr_slice)
        if alloc is None:
            print(f"[INFO] {vr_slice}_slice is not in auction_history.csv: the upf4 resources were fixed (no CPU/BW panels)")
        timeline = self.create_vr_timeline(vr_df, events_df, alloc)
        print(timeline.to_string(index=False))
        timeline.to_csv(self.vr_correlation_file, index=False)
        print(f"\nVR timeline saved to {self.vr_correlation_file}")
        self.generate_vr_graph(vr_df, events_df, timeline, alloc)

    def generate_report(self):
        """Generates unified report and creates graphs."""
        print("\n" + "="*70)
        print("CORRELATION ANALYSIS: AUCTION + QoE")
        print("="*70)
        
        auction_df, events_df, timeseries_df = self.load_data()
        
        print("\n--- QoE STATISTICS BY SLICE ---")
        qoe_stats = self.analyze_qoe_by_slice(timeseries_df)
        if qoe_stats is not None:
            print(qoe_stats)
        
        print("\n--- AUCTION RESULTS ---")
        auction_stats = self.analyze_auction_outcomes(auction_df)
        if auction_stats is not None:
            print(auction_stats)
        
        print("\n--- CORRELATED TIMELINE ---")
        timeline = self.create_correlation_timeline(auction_df, events_df, timeseries_df)
        if timeline is not None:
            print(timeline.head(20))
            timeline.to_csv(self.correlation_file, index=False)
            print(f"\nTimeline saved to {self.correlation_file}")
            
        # <<< CALLING GRAPH CREATION HERE >>>
        self.generate_graphs(auction_df, events_df, timeseries_df)

        self.report_vr(auction_df, events_df)

        print("\n" + "="*70)
        print("Report and Graphs completed!")
        print("="*70)

if __name__ == "__main__":
    input_dir = sys.argv[1] if len(sys.argv) > 1 else "resultados/"
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "resultados/teste/"
    os.makedirs(output_dir, exist_ok=True)
    correlator = DataCorrelator(input_dir, output_dir)
    correlator.generate_report()