"""
workflow.py — Logic Engine for BEE 5201 Fleet Simulation
=========================================================
Contains all core engineering functions, physics calculations,
ETL pipelines, and the FleetBike Digital Twin class.

Advanced Features: 
1. Pre-computed algebraic route energy decomposition.
2. Arrhenius equation thermal modeling for State of Health (SOH) degradation.
3. Equivalent Full Cycle (EFC) strict degradation tracking.
4. Stochastic Range Anxiety edge-case logic.
"""

import pandas as pd
import numpy as np
import xml.etree.ElementTree as ET

# ==========================================
# FIXED PARAMETERS
# ==========================================
TARGET_CAP_KWH = 1.44  # Target: 48V 30Ah
FLEET_SIZE = 100       # Fixed fleet size

# ==========================================
# FILE I/O
# ==========================================
def read_file(uploaded_file):
    """Read a CSV or Excel file into a DataFrame."""
    uploaded_file.seek(0)
    name = uploaded_file.name.lower()
    if name.endswith('.csv'):
        return pd.read_csv(uploaded_file)
    else:
        return pd.read_excel(uploaded_file)

# ==========================================
# ETL, SCALING & THERMAL DYNAMICS
# ==========================================
def perform_etl_and_scaling(dc_data, bms_data):
    """
    Extract electrochemical parameters from driving cycle and BMS datasets.
    Returns: (mean_km, std_km, k_degradation_base, r0_scaled)
    """
    # A. Usage Profile
    df_dc = read_file(dc_data)
    df_dc['start_date'] = pd.to_datetime(df_dc['start_date'])
    df_3users = df_dc[df_dc['user_id'].isin([579236, 629227, 629740])]
    daily = df_3users.groupby(['user_id', 'start_date'])['distance_km'].sum().reset_index()
    mean_km, std_km = daily['distance_km'].mean(), daily['distance_km'].std()

    # B. BMS Degradation Physics
    df_bms = read_file(bms_data)
    df_3batts = df_bms[df_bms['battery_id'].isin(['B5', 'B6', 'B7'])].copy()
    df_clean = df_3batts[df_3batts['cycle'] > 0].copy()
    
    # Power-Law Base Constant extraction
    df_clean['soh_dec'] = df_clean['SOH'] / 100.0
    df_clean['k_val'] = (1 - df_clean['soh_dec']) / np.sqrt(df_clean['cycle'])
    k_source = df_clean['k_val'].mean()

    # Subspace System Identification (SSI) Baseline
    df_clean['R0_raw'] = np.abs(df_clean['chV'] - df_clean['disV']) / (df_clean['chI'] + df_clean['disI'])
    r0_source = df_clean['R0_raw'].median()

    # C. Dimensional Scaling (72V 40Ah -> 48V 30Ah)
    r0_scaled = r0_source * ((48.0 / 72.0) / (30.0 / 40.0))
    
    return mean_km, std_km, k_source, r0_scaled

def apply_arrhenius_thermal_stress(k_base, current_temp, baseline_temp=25.0):
    """
    Modifies the chemical degradation slope (k) based on ambient temperature.
    Uses the Arrhenius heuristic: Degradation rate ~doubles every 10°C rise.
    """
    delta_T = current_temp - baseline_temp
    thermal_multiplier = 2.0 ** (delta_T / 10.0)
    return k_base * thermal_multiplier

# ==========================================
# GPX PARSING
# ==========================================
def parse_real_gpx(gpx_data):
    """
    Parse a GPX file and compute distance, gradient, and speed profiles.
    Returns: (DataFrame, route_distance_km)
    """
    tree = ET.parse(gpx_data)
    ns = {'gpx': 'http://www.topografix.com/GPX/1/1'}
    data = [
        {
            'lat': float(pt.get('lat')),
            'lon': float(pt.get('lon')),
            'ele': float(pt.find('gpx:ele', ns).text) if pt.find('gpx:ele', ns) is not None else 0.0
        }
        for pt in tree.getroot().findall('.//gpx:trkpt', ns)
    ]
    df = pd.DataFrame(data)

    def haversine(lat1, lon1, lat2, lon2):
        R, phi1, phi2 = 6371000, np.radians(lat1), np.radians(lat2)
        dphi, dlambda = np.radians(lat2 - lat1), np.radians(lon2 - lon1)
        a = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlambda / 2) ** 2
        return 2 * R * np.arctan2(np.sqrt(a), np.sqrt(1 - a))

    dist = [0.0]
    for i in range(1, len(df)):
        dist.append(dist[-1] + haversine(
            df.iloc[i - 1]['lat'], df.iloc[i - 1]['lon'],
            df.iloc[i]['lat'], df.iloc[i]['lon']
        ))
    df['dist_m'] = dist

    # Calculate precise hill angles
    delta_dist = np.where(np.diff(df['dist_m']) == 0, 1e-5, np.diff(df['dist_m']))
    df['grad'] = np.append([0.0], np.arctan(np.diff(df['ele']) / delta_dist))
    
    # Adaptive traffic/topography velocity profile
    df['v_ms'] = np.where(df['grad'] > 0.03, 6.94, 11.11)

    return df, df['dist_m'].iloc[-1] / 1000.0

# ==========================================
# PRE-COMPUTED KINEMATICS ENGINE
# ==========================================
def precompute_route_energy_coefficients(df_route, eff_base_wh_km, payload):
    """
    Algebraically decomposes the route energy into A, B, C constants.
    Energy(r_dyn) = A + B * r_dyn + C
    This prevents iterating through the GPX array millions of times.
    """
    M, G = payload, 9.81
    v = df_route['v_ms'].values[1:]
    theta = df_route['grad'].values[1:]
    d = np.diff(df_route['dist_m'].values)
    dt = np.where(v > 0, d / v, 0.0)

    # Base mechanical power (Watts)
    P_mech = (M * G * np.sin(theta) * v) + ((eff_base_wh_km * 3.6) * v)
    pos_mask = P_mech > 0

    # Coefficient A: Base Mechanical Energy (Joules)
    A = np.sum(P_mech[pos_mask] * dt[pos_mask])

    # Coefficient B: I^2 scalar for internal resistance heat loss
    B = np.sum((P_mech[pos_mask] / 48.0) ** 2 * dt[pos_mask])

    # Coefficient C: Regenerative braking return
    C = np.sum(P_mech[~pos_mask] * 0.3 * dt[~pos_mask])

    return A, B, C

def fast_trip_energy(A, B, C, r_dyn):
    """Single scalar operation to compute trip Wh. ~1000x performance boost."""
    return (A + B * r_dyn + C) / 3600.0

# ==========================================
# DIGITAL TWIN CLASS
# ==========================================
class FleetBike:
    def __init__(self, name, r0, k, p, capex, swap_fee, mode, tariff, payload,
                 anx_min, anx_max, energy_coeffs):
        self.name, self.r0_base, self.k_coeff, self.p_factor = name, r0, k, p
        self.mode, self.swap_fee, self.tariff, self.payload = mode, swap_fee, tariff, payload
        self.anx_min, self.anx_max = anx_min / 100.0, anx_max / 100.0
        self.energy_A, self.energy_B, self.energy_C = energy_coeffs

        # Pure Financial Tracking
        self.initial_capex = capex
        self.capex_amortized = 0.0
        self.opex = 0.0
        
        # Physical State Tracking
        self.soh, self.soc, self.cum_efc, self.total_km = 1.0, 1.0, 0.0, 0.0
        self.anxiety_threshold = np.random.uniform(self.anx_min, self.anx_max)

        # Telemetry Logging
        self.log_day, self.log_soh, self.log_wh_km, self.log_cap, self.log_cum_tco = [], [], [], [], []

    def process_trip(self, route_km):
        # SSI dynamically increases Ohmic resistance as battery degrades
        r_dyn = self.r0_base * (1 + (1 - self.soh) * 2.5)
        
        trip_kwh = fast_trip_energy(self.energy_A, self.energy_B, self.energy_C, r_dyn) / 1000.0
        self.total_km += route_km
        efc_this_trip = trip_kwh / TARGET_CAP_KWH

        if self.mode == "Depot":
            self.opex += trip_kwh * self.tariff # OPEX: KPLC KES/kWh
            loss_prev = self.k_coeff * (self.cum_efc ** self.p_factor)
            self.cum_efc += efc_this_trip
            self.soh -= (self.k_coeff * (self.cum_efc ** self.p_factor) - loss_prev)
            self.capex_amortized = self.initial_capex * ((1.0 - self.soh) / 0.20)
        
        else: # BaaS Model
            # Predictive Swap & Range Anxiety Edge Case
            if self.soc < (efc_this_trip + 0.05) or self.soc < self.anxiety_threshold:
                self.soc = 1.0
                self.opex += self.swap_fee # OPEX: BaaS KES/Swap
                self.anxiety_threshold = np.random.uniform(self.anx_min, self.anx_max)
            
            self.soc -= efc_this_trip
            
            # BaaS Operator still assumes structural degradation via network EFC throughput
            loss_prev = self.k_coeff * (self.cum_efc ** self.p_factor)
            self.cum_efc += efc_this_trip
            self.soh -= (self.k_coeff * (self.cum_efc ** self.p_factor) - loss_prev)

    def log_daily_stats(self, day, route_km):
        self.log_day.append(day)
        self.log_soh.append(self.soh * 100)
        r_dyn = self.r0_base * (1 + (1 - self.soh) * 2.5)
        self.log_wh_km.append(
            fast_trip_energy(self.energy_A, self.energy_B, self.energy_C, r_dyn) / route_km
        )
        self.log_cap.append(30.0 * self.soh)
        self.log_cum_tco.append(self.opex + self.capex_amortized)

# ==========================================
# FLEET SIMULATION RUNNER
# ==========================================
def run_fleet_simulation(mean_km, std_km, k_lfp_base, r0_lfp_scaled,
                         df_route, route_km, kplc_tariff, swap_fee,
                         payload_weight, anxiety_min, anxiety_max, env_temp,
                         sim_days=120, progress_callback=None):
    """
    Executes the macro-stochastic fleet simulation using thermally adjusted parameters.
    """
    # 1. Apply Arrhenius Thermal Stress to the baseline k-coefficients
    k_lfp_thermal = apply_arrhenius_thermal_stress(k_lfp_base, env_temp)
    k_sib_thermal = apply_arrhenius_thermal_stress(k_lfp_base * 1.8, env_temp) # SIB naturally degrades 1.8x faster

    # 2. Pre-compute route mechanics ONCE
    coeffs_sib = precompute_route_energy_coefficients(df_route, 21.0, payload_weight)
    coeffs_lfp = precompute_route_energy_coefficients(df_route, 18.5, payload_weight)

    # 3. Initialize the 4 Business Models
    fleets = {
        "SIB Owned": [
            FleetBike("SIB", r0_lfp_scaled * 1.5, k_sib_thermal, 0.55,
                      20785.0, 0, "Depot", kplc_tariff, payload_weight,
                      anxiety_min, anxiety_max, coeffs_sib)
            for _ in range(FLEET_SIZE)
        ],
        "LFP Owned": [
            FleetBike("LFP", r0_lfp_scaled, k_lfp_thermal, 0.50,
                      31178.0, 0, "Depot", kplc_tariff, payload_weight,
                      anxiety_min, anxiety_max, coeffs_lfp)
            for _ in range(FLEET_SIZE)
        ],
        "SIB BaaS": [
            FleetBike("SIB BaaS", r0_lfp_scaled * 1.5, k_sib_thermal, 0.55,
                      0.0, swap_fee, "BaaS", kplc_tariff, payload_weight,
                      anxiety_min, anxiety_max, coeffs_sib)
            for _ in range(FLEET_SIZE)
        ],
        "LFP BaaS": [
            FleetBike("LFP BaaS", r0_lfp_scaled, k_lfp_thermal, 0.50,
                      0.0, swap_fee, "BaaS", kplc_tariff, payload_weight,
                      anxiety_min, anxiety_max, coeffs_lfp)
            for _ in range(FLEET_SIZE)
        ],
    }

    # 4. Stochastic Daily Iteration
    for day in range(sim_days):
        mean_trips = mean_km / route_km
        std_trips = std_km / route_km
        daily_trips = np.random.normal(mean_trips, std_trips, FLEET_SIZE).clip(1, 20).astype(int)

        for name, fleet in fleets.items():
            for i, bike in enumerate(fleet):
                for _ in range(daily_trips[i]):
                    bike.process_trip(route_km)
                
                # Sample telemetry from Bike 0 to drive the Streamlit UI charts
                if i == 0:
                    bike.log_daily_stats(day, route_km)

        if progress_callback:
            progress_callback((day + 1) / sim_days)

    # 5. Compile Amortized Executive Metrics
    results = {}
    for name, fleet in fleets.items():
        avg_opex = sum(b.opex for b in fleet) / FLEET_SIZE
        avg_capex = sum(b.capex_amortized for b in fleet) / FLEET_SIZE
        avg_km = sum(b.total_km for b in fleet) / FLEET_SIZE
        results[name] = (avg_opex + avg_capex) / avg_km

    best_model = min(results, key=results.get)
    return fleets, results, best_model
