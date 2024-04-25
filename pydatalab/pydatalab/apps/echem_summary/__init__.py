import os
from pathlib import Path

import bokeh.embed
import pandas as pd
from bokeh.models import HoverTool, Span
from bokeh.plotting import figure
from bokeh.layouts import gridplot
from io import StringIO
import re

from pydatalab.blocks.base import DataBlock
from pydatalab.bokeh_plots import DATALAB_BOKEH_THEME, selectable_axes_plot, TOOLS
from pydatalab.file_utils import get_file_info_by_id
from pydatalab.logger import LOGGER
from pydatalab.mongo import flask_mongo
from .utils import extract_echem_features


class EchemSumBlock(DataBlock):
    accepted_file_extensions = [
        ".csv", 
        ".xlsx", 
        ".xls"
    ]
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
        return (self.generate_echem_summary,)

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
        
        synth_table = None
        if echem_summary_data is not None:
            negative_electrode_id = self._get_negative_electrode()
            if negative_electrode_id is not None:
                synthesis_description = self._get_synth_description(negative_electrode_id)
                synth_table = self._extract_synth_table(synthesis_description)
            
            plots = []
            curve_names = ["discharge", "charge"]
            for name in curve_names:
                curve_df = echem_summary_data[f"{name}_df"]
                plot = selectable_axes_plot(
                    curve_df,
                    x_options=["Capacity/mAh/g"],
                    y_options=["Voltage/V"],
                    plot_points=False,
                    plot_line=True,
                    plot_title=f"{name.capitalize()} curve",
                    aspect_ratio=1.5,
                )
                plot_figure = plot.children[1]
                plot_figure.renderers[0].name = f'{name}_plot'  # have to add name parameter to plot so that hover tool doesn't duplicate on marker point
                plot_figure.add_tools(HoverTool(names=[f'{name}_plot'], tooltips=[("Specific Capacity", "@x"), ("Voltage", "@y")]))
                plot_figure.line(echem_summary_data[f"{name}_plot"][0], echem_summary_data[f"{name}_plot"][1], color='blue', line_width=2, name=f"{name}_plot")
                plot_figure.scatter(x=echem_summary_data[f"{name}_plateau"][0], y=echem_summary_data[f"{name}_plateau"][1], color='red', size=20, marker='x')
                plateau = Span(location=echem_summary_data[f"{name}_plateau"][1], dimension='width', line_color='red', line_width=2, line_dash='dotted')
                plot_figure.add_layout(plateau)
                plots.append(plot)
            
            p = gridplot([plots], sizing_mode="scale_width")

            if synth_table is not None:
                summary_df = pd.concat([echem_summary_data['table'], synth_table], axis=0, ignore_index=True)
            else:
                summary_df = echem_summary_data['table']
            summary_html_table = summary_df.to_html(border=1, index=False)


            self.data["bokeh_plot_data"] = bokeh.embed.json_item(p, theme=DATALAB_BOKEH_THEME)
            self.data["freeform_comment"] = summary_html_table