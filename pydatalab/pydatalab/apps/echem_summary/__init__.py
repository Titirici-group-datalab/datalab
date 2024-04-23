import os
from pathlib import Path

import bokeh.embed
import pandas as pd
from bokeh.models import HoverTool, LogColorMapper
from io import StringIO
import lxml
import re

from pydatalab.blocks.base import DataBlock
from pydatalab.bokeh_plots import DATALAB_BOKEH_THEME, selectable_axes_plot
from pydatalab.file_utils import get_file_info_by_id
from pydatalab.logger import LOGGER
from pydatalab.mongo import flask_mongo
from .utils import extract_echem_features


def parse_ivium_eis_txt(filename: Path):
    eis = pd.read_csv(filename, sep="\t")
    eis["Z2 /ohm"] *= -1
    eis.rename(
        {"Z1 /ohm": "Re(Z) [Ω]", "Z2 /ohm": "-Im(Z) [Ω]", "freq. /Hz": "Frequency [Hz]"},
        inplace=True,
        axis="columns",
    )
    return eis


class EchemSumBlock(DataBlock):
    accepted_file_extensions = [".csv", ".xlsx", ".xls"]
    blocktype = "echem_sum"
    name = "Electrochemistry Summary"
    description = "Electrochemistry Summary"

    def _get_negative_electrode(self):
        # Get the negative electrode item_id for this item
        doc = flask_mongo.db.items.find_one(
            {"item_id": self.data["item_id"]}, {"negative_electrode": 1}
        )
        doc = doc.get("negative_electrode", None)
        if doc is not None:
            negative_electrode_id = doc[0]["item"].get("item_id", None)
            return negative_electrode_id
        return None
    
    def _get_synth_description(self, negative_electrode_id):
        # Get the synthesis description for the negative electrode
        doc = flask_mongo.db.items.find_one(
            {"item_id": negative_electrode_id}, {"synthesis_description": 1}
        )
        synthesis_description = doc.get("synthesis_description", None)
        return synthesis_description
    
    def _extract_synth_table(self, synthesis_description):
        # Extract the synthesis table from the synthesis description
        table_regex = r'<table[^>]*>(.*?)</table>'
        tables = re.findall(table_regex, synthesis_description, flags=re.IGNORECASE | re.DOTALL)
        if len(tables) == 0:
            return None
        synth_table = pd.read_html(StringIO(synthesis_description), header=0)[0]
        return synth_table
    

    @property
    def plot_functions(self):
        return (self.generate_echem_summary)

    def generate_echem_summary(self):
        file_info = None
        echem_summary_data = None

        if "file_id" not in self.data:
            LOGGER.warning("No file set in the DataBlock")
            return
        else:
            file_info = get_file_info_by_id(self.data["file_id"], update_if_live=True)
            ext = os.path.splitext(file_info["location"].split("/")[-1])[-1].lower()
            if ext not in self.accepted_file_extensions:
                LOGGER.warning(
                    "Unsupported file extension (must be one of %s, not %s)",
                    self.accepted_file_extensions,
                    ext,
                )
                return
            
            echem_summary_data = extract_echem_features(Path(file_info["location"]))
        
        if echem_summary_data is not None:
            negative_electrode_id = self._get_negative_electrode()
            if negative_electrode_id is not None:
                synthesis_description = self._get_synth_description(negative_electrode_id)
                synth_table = self._extract_synth_table(synthesis_description)
            
            # add plot
            # add merging tables and converting to html
            # print message if no synth data



    @property
    def plot_functions(self):
        return (self.generate_eis_plot,)

    def generate_eis_plot(self):
        file_info = None
        # all_files = None
        eis_data = None

        if "file_id" not in self.data:
            LOGGER.warning("No file set in the DataBlock")
            return
        else:
            file_info = get_file_info_by_id(self.data["file_id"], update_if_live=True)
            ext = os.path.splitext(file_info["location"].split("/")[-1])[-1].lower()
            if ext not in self.accepted_file_extensions:
                LOGGER.warning(
                    "Unsupported file extension (must be one of %s, not %s)",
                    self.accepted_file_extensions,
                    ext,
                )
                return

            eis_data = parse_ivium_eis_txt(Path(file_info["location"]))

        if eis_data is not None:
            plot = selectable_axes_plot(
                eis_data,
                x_options=["Re(Z) [Ω]"],
                y_options=["-Im(Z) [Ω]"],
                color_options=["Frequency [Hz]"],
                color_mapper=LogColorMapper("Cividis256"),
                plot_points=True,
                plot_line=False,
                tools=HoverTool(tooltips=[("Frequency [Hz]", "@{Frequency [Hz]}")]),
            )

            self.data["bokeh_plot_data"] = bokeh.embed.json_item(plot, theme=DATALAB_BOKEH_THEME)
