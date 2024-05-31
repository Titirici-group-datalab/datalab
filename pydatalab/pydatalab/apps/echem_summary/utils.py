import os
from pathlib import Path

import pandas as pd
import numpy as np
from scipy.signal import savgol_filter
from scipy.interpolate import splev, splrep


def landt_file_loader(filepath, process=True):
    extension = os.path.splitext(filepath)[-1].lower()
    # add functionality to read csv as it will be quicker
    if extension == ".xlsx":
        xlsx = pd.ExcelFile(filepath, engine="openpyxl")
        df = xlsx_process(xlsx)
    elif extension == ".xls":
        xlsx = pd.ExcelFile(filepath)
        df = xlsx_process(xlsx)
    elif extension == ".csv":
        df = pd.read_csv(filepath)
        if df.columns[0] != "Record":
            raise ValueError("CSV file in wrong format")

    df = process_dataframe(df) if process else df
    return df


def xlsx_process(xlsx):
    sheet_names = xlsx.sheet_names
    if len(sheet_names) == 1:  # if only one sheet, use that sheet
        df = xlsx.parse(sheet_names[0])
        if check_cycle_split(df):
            df = multi_column_handler(xlsx, 0)
    else:
        record_tab = find_record_tab(sheet_names)  # find the sheet with the record tab
        if record_tab is not None:
            df = xlsx.parse(sheet_names[record_tab])
            if check_cycle_split(df):
                df = multi_column_handler(xlsx, record_tab)
        else:
            raise ValueError("No sheet with record tab found in file")
    return df


def find_record_tab(sheet_names):
    # Finds the index of the sheet with the record tab
    for i in range(len(sheet_names)):
        if "record" in sheet_names[i].lower():
            return i


def check_cycle_split(df):
    # Checks if the data is split into multiple columns
    if "Cycle" in df.columns[0]:
        return True
    else:
        return False


def multi_column_handler(xlsx, record_tab):
    # Handles the multi-column data
    df = xlsx.parse(record_tab, header=[0, 1])
    df.drop(
        ["EnergyD"], axis=1, level=1, inplace=True
    )  # Drop column that only appears in last cycle
    if len(df.columns.levels) > 1:
        frames = []
        for i in df.columns.get_level_values(0).unique():  # Splits df by cycle
            # The following 2 lines remove empty rows and rows where only the 'Record' column is filled
            new_df = df[i].dropna(how="all")
            new_df = new_df[
                ~(new_df["Record"] != np.nan)
                | ~(new_df.drop("Record", axis=1).isna().all(axis=1))
            ]
            frames.append(new_df)
        new_df = pd.concat(frames, axis=0)
    return new_df


def process_dataframe(df):
    # Process the DataFrame
    # Elements taken from old_land_processing function from BenSmithGreyGroup navani

    df = df[df["Current/mA"].apply(type) != str]
    df = df[pd.notna(df["Current/mA"])]

    def land_state(x):
        # 1 is positive current and 0 is negative current
        if x > 0:
            return 1
        elif x < 0:
            return 0
        elif x == 0:
            return "R"
        else:
            print(x)
            raise ValueError("Unexpected value in current - not a number")

    df["state"] = df["Current/mA"].map(lambda x: land_state(x))
    not_rest_idx = df[df["state"] != "R"].index
    df.loc[not_rest_idx, "cycle change"] = df.loc[not_rest_idx, "state"].ne(
        df.loc[not_rest_idx, "state"].shift()
    )
    df["half cycle"] = (df["cycle change"] == True).cumsum()
    df["full cycle"] = (df["half cycle"] / 2).apply(np.ceil)

    columns_to_keep = [
        "Current/mA",
        "Capacity/mAh",
        "state",
        "SpeCap/mAh/g",
        "Voltage/V",
        "dQ/dV/mAh/V",
    ]

    new_df = df.copy()
    new_df = new_df[columns_to_keep]
    new_df["CycleNo"] = df["full cycle"]

    return new_df


def invert_charge_discharge(df):
    # Inverts charge and discharge cycles 0 becomes positive current and 1 becomes negative current
    df.loc[df["state"] == 0, "state"] = 2
    df.loc[df["state"] == 1, "state"] = 0
    df.loc[df["state"] == 2, "state"] = 1
    return df


def clean_signal(
    voltage,
    capacity,
    dqdv,
    polynomial_spline=3,
    s_spline=1e-5,
    polyorder_1=5,
    window_size_1=101,
    polyorder_2=5,
    window_size_2=1001,
):
    # Function that cleans the raw voltage, cap and dqdv data so can get smooth curves and derivatives

    df = pd.DataFrame({"voltage": voltage, "capacity": capacity, "dqdv": dqdv})
    unique_v = (
        df.astype(float).groupby("voltage").mean().index
    )  # get unique voltage values
    unique_v_cap = df.astype(float).groupby("voltage").mean()["capacity"]
    unique_v_dqdv = df.astype(float).groupby("voltage").mean()["dqdv"]

    x_volt = np.linspace(unique_v.min(), unique_v.max(), num=int(1e4))

    spl_cap = splrep(unique_v, unique_v_cap, k=1, s=1.0)
    cap = splev(x_volt, spl_cap)
    smooth_cap = savgol_filter(cap, window_size_1, polyorder_1)

    spl = splrep(unique_v, unique_v_dqdv, k=1, s=1.0)
    y_dqdq = splev(x_volt, spl)
    smooth_dqdv = savgol_filter(y_dqdq, window_size_1, polyorder_1)
    smooth_spl_dqdv = splrep(x_volt, smooth_dqdv, k=polynomial_spline, s=s_spline)
    dqdv_2 = splev(x_volt, smooth_spl_dqdv, der=1)
    smooth_dqdv_2 = savgol_filter(dqdv_2, window_size_2, polyorder_2)
    peak_val = max(smooth_dqdv.min(), smooth_dqdv.max(), key=abs)
    peak_idx = np.where(smooth_dqdv == peak_val)[0]
    return (
        x_volt,
        smooth_cap,
        smooth_dqdv_2,
        peak_idx,
    )  # need to return peak index to ignore very low volt data


def check_state(dqdv):
    # Check if dqdv from discharge or charge (negative or positive peak)
    peak_val = max(dqdv.min(), dqdv.max(), key=abs)
    if peak_val > 0:
        return 1
    elif peak_val < 0:
        return 0
    else:
        return "R"
    

def find_plat_cap_2(voltage, capacity, dqdv):
    # Second iteration of finding the plateau capacity, takes min of 2nd derivative for charge and point in between max/min inflection points for discharge
    # Preferred method as gives better results for discharge
    x_volt, smooth_cap, smooth_dqdv_2, peak_idx = clean_signal(voltage, capacity, dqdv)
    state = check_state(dqdv)
    if state == 1:
        plat_cap = smooth_cap[smooth_dqdv_2[peak_idx[0]:].argmin() + peak_idx[0]]
    elif state == 0:
        min_peak = smooth_dqdv_2[peak_idx[0]:].argmin() + peak_idx[0]
        max_peak = smooth_dqdv_2[peak_idx[0]:].argmax() + peak_idx[0]
        plat_point = round((min_peak + max_peak) / 2, 0).astype(int)
        plat_cap = smooth_cap.max() - smooth_cap[plat_point]
    else:
        plat_cap = np.nan
    return plat_cap, x_volt, smooth_cap


def get_inflection_point(plat_cap, x_volt, smooth_cap, state):
    # Get the inflection point for volt vs cap curve
    if state == 0:
        inf_point = np.argmin(np.abs(smooth_cap - (smooth_cap.max() - plat_cap)))
    elif state == 1:
        inf_point = np.argmin(np.abs(smooth_cap - plat_cap))
    return inf_point


def extract_echem_features(filepath, cycle_no=1, invert=False):
    df = landt_file_loader(filepath)
    if invert:
        df = invert_charge_discharge(df)
    volt_0 = df.loc[(df["CycleNo"] == cycle_no) & (df["state"] == 0)][
        "Voltage/V"
    ].values
    cap_0 = df.loc[(df["CycleNo"] == cycle_no) & (df["state"] == 0)][
        "SpeCap/mAh/g"
    ].values
    dqdv_0 = df.loc[(df["CycleNo"] == cycle_no) & (df["state"] == 0)][
        "dQ/dV/mAh/V"
    ].values
    volt_1 = df.loc[(df["CycleNo"] == cycle_no) & (df["state"] == 1)][
        "Voltage/V"
    ].values
    cap_1 = df.loc[(df["CycleNo"] == cycle_no) & (df["state"] == 1)][
        "SpeCap/mAh/g"
    ].values
    dqdv_1 = df.loc[(df["CycleNo"] == cycle_no) & (df["state"] == 1)][
        "dQ/dV/mAh/V"
    ].values
    plat_cap_0, x_volt_0, smooth_cap_0 = find_plat_cap_2(volt_0, cap_0, dqdv_0)
    plat_cap_1, x_volt_1, smooth_cap_1 = find_plat_cap_2(volt_1, cap_1, dqdv_1)

    ice = {"Parameter": "ICE", "Value": round(cap_1.max() / cap_0.max(), 4)}
    charge_cap = {"Parameter": "Charge SpeCap/mAh/g", "Value": round(cap_1.max(), 2)}
    discharge_plat_cap = {
        "Parameter": "Discharge plateau SpeCap/mAh/g",
        "Value": round(plat_cap_0, 2),
    }
    charge_plat_cap = {
        "Parameter": "Charge plateau SpeCap/mAh/g",
        "Value": round(plat_cap_1, 2),
    }
    echem_df = pd.DataFrame([ice, charge_cap, discharge_plat_cap, charge_plat_cap])
    summary = {}
    summary['table'] = echem_df
    summary['discharge_plot'] = (smooth_cap_0, x_volt_0)
    summary['charge_plot'] = (smooth_cap_1, x_volt_1)
    inf_point_0 = get_inflection_point(plat_cap_0, x_volt_0, smooth_cap_0, 0)
    inf_point_1 = get_inflection_point(plat_cap_1, x_volt_1, smooth_cap_1, 1)
    summary['discharge_plateau'] = (smooth_cap_0[inf_point_0], x_volt_0[inf_point_0])
    summary['charge_plateau'] = (smooth_cap_1[inf_point_1], x_volt_1[inf_point_1])
    discharge_df = pd.DataFrame({"Voltage/V": x_volt_0, "Capacity/mAh/g": smooth_cap_0})
    charge_df = pd.DataFrame({"Voltage/V": x_volt_1, "Capacity/mAh/g": smooth_cap_1})
    summary['discharge_df'] = discharge_df
    summary['charge_df'] = charge_df
    return summary