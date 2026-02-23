import streamlit as st
import pandas as pd
import plotly.express as px
from workflow import (
    perform_etl_and_scaling, parse_real_gpx, run_fleet_simulation
)

# ==========================================
# PAGE CONFIGURATION
# ==========================================
st.set_page_config(page_title="BEE 5201: Fleet Simulation", layout="wide", initial_sidebar_state="expanded")

st.title("⚡ BEE 5201: A Fleet Simulation and Techno-Economic Analysis")
st.markdown("E-BIKE TCO Digital Twin")

# ==========================================
# SIDEBAR: THE CONTROL CENTER
# ==========================================

# --- Step 1: Data Upload ---
st.sidebar.header("📂 1. Data Input")
dc_file = st.sidebar.file_uploader("Upload Driving Cycles (CSV/Excel)", type=['csv', 'xlsx', 'xls'])
bms_file = st.sidebar.file_uploader("Upload BMS Data (CSV/Excel)", type=['csv', 'xlsx', 'xls'])
gpx_file = st.sidebar.file_uploader("Upload Route Topography (GPX)", type=['gpx', 'xml'])

# --- ETL Button ---
etl_ready = dc_file and bms_file and gpx_file
etl_btn = st.sidebar.button("📥 Perform ETL", use_container_width=True,
                             disabled=not etl_ready,
                             help="Extract parameters from uploaded files")

# --- Step 2: Parameters ---
st.sidebar.header("⚙️ 2. Financial & Physical Parameters")
kplc_tariff = st.sidebar.slider("KPLC Grid Tariff (KSh/kWh)", min_value=10.0, max_value=40.0, value=16.0, step=1.0)
swap_fee = st.sidebar.slider("BaaS Swap Fee (KSh)", min_value=100.0, max_value=300.0, value=206.0, step=1.0)
payload_weight = st.sidebar.slider("Payload: Bike + Rider + Cargo (kg)", min_value=150.0, max_value=300.0, value=200.0, step=10.0)

st.sidebar.header("🧠 3. Range Anxiety Logic")
anxiety_min = st.sidebar.slider("Min Swap Threshold (SOC %)", 10, 30, 20)
anxiety_max = st.sidebar.slider("Max Swap Threshold (SOC %)", 20, 50, 35)

st.sidebar.header("🌡️ 4. Thermal Environment")
env_temp = st.sidebar.slider("Ambient Temperature (°C)", min_value=15.0, max_value=27.0, value=25.0, step=1.0,
                              help="Arrhenius model: degradation rate ~doubles every 10°C above 25°C baseline")

st.sidebar.header("📊 5. Simulation Duration")
sim_days = st.sidebar.selectbox("Number of Days", options=[40, 80, 120], index=0,
                                help="40 days for quick trends, 120 for full feasibility window")

# --- Run Simulation Button ---
st.sidebar.divider()
sim_ready = 'etl_done' in st.session_state and st.session_state.etl_done
run_sim = st.sidebar.button("🚀 Run Simulation", use_container_width=True, type="primary",
                             disabled=not sim_ready,
                             help="Run ETL first to enable this button")

# ==========================================
# ETL PROCESSING
# ==========================================
if etl_btn and etl_ready:
    with st.spinner("📥 Extracting Electrochemical DNA and parsing Topography..."):
        mean_km, std_km, k_lfp, r0_scaled = perform_etl_and_scaling(dc_file, bms_file)
        df_route, route_km = parse_real_gpx(gpx_file)

        # Cache results in session state
        st.session_state.etl_done = True
        st.session_state.mean_km = mean_km
        st.session_state.std_km = std_km
        st.session_state.k_lfp = k_lfp
        st.session_state.r0_scaled = r0_scaled
        st.session_state.df_route = df_route
        st.session_state.route_km = route_km

    st.rerun()

# ==========================================
# ETL RESULTS DISPLAY
# ==========================================
if sim_ready:
    st.success("✅ ETL Complete — Parameters extracted and cached.")
    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("Mean Daily Distance", f"{st.session_state.mean_km:.1f} km")
    col_b.metric("Std Deviation", f"{st.session_state.std_km:.1f} km")
    col_c.metric("Degradation k", f"{st.session_state.k_lfp:.6f}")
    col_d.metric("R₀ Scaled (Ω)", f"{st.session_state.r0_scaled:.6f}")

    st.caption(f"📍 Route: {st.session_state.route_km:.2f} km | "
               f"📊 Fleet: 100 bikes | "
               f"📅 Simulation: {sim_days} days | "
               f"🌡️ Temp: {env_temp}°C")
    st.divider()

# ==========================================
# SIMULATION
# ==========================================
if run_sim and sim_ready:
    progress_bar = st.progress(0, text="Initializing fleet...")

    def update_progress(pct):
        progress_bar.progress(pct, text=f"Simulating day {int(pct * sim_days)}/{sim_days}...")

    fleets, results, best_model = run_fleet_simulation(
        st.session_state.mean_km, st.session_state.std_km,
        st.session_state.k_lfp, st.session_state.r0_scaled,
        st.session_state.df_route, st.session_state.route_km,
        kplc_tariff, swap_fee, payload_weight, anxiety_min, anxiety_max,
        env_temp, sim_days=sim_days, progress_callback=update_progress
    )

    progress_bar.progress(1.0, text="✅ Simulation complete!")

    # --- THE EXECUTIVE SUMMARY ---
    st.success(f"### 🏆 Feasibility Verdict: **{best_model}** is the most cost-effective path at KSh {results[best_model]:.2f}/km.")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("SIB Owned TCO", f"KSh {results['SIB Owned']:.2f} / km")
    col2.metric("LFP Owned TCO", f"KSh {results['LFP Owned']:.2f} / km")
    col3.metric("SIB BaaS", f"KSh {results['SIB BaaS']:.2f} / km")
    col4.metric("LFP BaaS", f"KSh {results['LFP BaaS']:.2f} / km")
    st.divider()

    # --- VISUALIZATIONS ---
    rep_sib = fleets["SIB Owned"][0]
    rep_lfp = fleets["LFP Owned"][0]
    rep_sib_baas = fleets["SIB BaaS"][0]
    rep_lfp_baas = fleets["LFP BaaS"][0]

    # 1. Topography
    st.subheader("⛰️ GPX Route Topography (Physics Engine)")
    fig_topo = px.area(st.session_state.df_route, x='dist_m', y='ele',
                       labels={'dist_m': 'Distance (m)', 'ele': 'Elevation (m)'})
    fig_topo.update_layout(yaxis_range=[
        st.session_state.df_route['ele'].min() - 10,
        st.session_state.df_route['ele'].max() + 10
    ])
    st.plotly_chart(fig_topo, use_container_width=True)

    colA, colB = st.columns(2)

    # 2. SOH Degradation
    with colA:
        st.subheader("📉 Battery Health (SOH) Fade")
        df_soh = pd.DataFrame({
            'Day': rep_sib.log_day,
            'SIB Owned': rep_sib.log_soh,
            'LFP Owned': rep_lfp.log_soh
        })
        fig_soh = px.line(df_soh, x='Day', y=['SIB Owned', 'LFP Owned'],
                          labels={'value': 'SOH (%)', 'variable': 'Chemistry'})
        fig_soh.add_hline(y=80, line_dash="dash", line_color="red", annotation_text="End of Life (80%)")
        st.plotly_chart(fig_soh, use_container_width=True)

    # 3. Energy Efficiency (SSI Proof)
    with colB:
        st.subheader("⚡ Energy Efficiency (SSI Voltage Sag)")
        df_eff = pd.DataFrame({
            'Day': rep_sib.log_day,
            'SIB (Wh/km)': rep_sib.log_wh_km,
            'LFP (Wh/km)': rep_lfp.log_wh_km
        })
        fig_eff = px.line(df_eff, x='Day', y=['SIB (Wh/km)', 'LFP (Wh/km)'],
                          labels={'value': 'Wh / km consumed'})
        st.plotly_chart(fig_eff, use_container_width=True)

    colC, colD = st.columns(2)

    # 4. Capacity Ah Fade
    with colC:
        st.subheader("🔋 Capacity Fade (Ah)")
        df_cap = pd.DataFrame({
            'Day': rep_sib.log_day,
            'SIB (Ah)': rep_sib.log_cap,
            'LFP (Ah)': rep_lfp.log_cap
        })
        fig_cap = px.area(df_cap, x='Day', y=['SIB (Ah)', 'LFP (Ah)'],
                          labels={'value': 'Available Capacity (Ah)'})
        st.plotly_chart(fig_cap, use_container_width=True)

    # 5. Cumulative Cost Breakdown (all 4 models)
    with colD:
        st.subheader("💰 Cumulative TCO Accumulation")
        df_tco = pd.DataFrame({
            'Day': rep_sib.log_day,
            'SIB Owned': rep_sib.log_cum_tco,
            'LFP Owned': rep_lfp.log_cum_tco,
            'SIB BaaS': rep_sib_baas.log_cum_tco,
            'LFP BaaS': rep_lfp_baas.log_cum_tco
        })
        fig_tco = px.line(df_tco, x='Day', y=['SIB Owned', 'LFP Owned', 'SIB BaaS', 'LFP BaaS'],
                          labels={'value': 'Total Spend (KSh)'})
        st.plotly_chart(fig_tco, use_container_width=True)

elif not sim_ready and not etl_btn:
    st.info("👈 Upload your three data files, then click **📥 Perform ETL** to extract parameters before running the simulation.")
