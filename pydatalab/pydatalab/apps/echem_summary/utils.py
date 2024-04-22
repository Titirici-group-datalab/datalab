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
