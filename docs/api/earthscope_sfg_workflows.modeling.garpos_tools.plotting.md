# plotting

`earthscope_sfg_workflows.modeling.garpos_tools.plotting`

Day-of-year plotting helpers for GARPOS results.

## class `DOYPlotter`

Multi-day plotter that aggregates `DOYResult`s into time-series plots.

**Methods**

### `DOYPlotter.make_survey_image(self, start_date: datetime.datetime = None, end_date: datetime.datetime = None, survey_type='survey', survey_name='survey', filepath='survey_image.png')`

Generate a plot of antenna positions along east/north axis with transponder positions

1. plot transponder east, north position as markers
2. plot antenna east, north position as line plot (from self.df_merged)

### `DOYPlotter.make_ts_plots(self, start_date: datetime.datetime = None, end_date: datetime.datetime = None, filepath='ts_plot.png')`

Plot range/time residuals over `[start_date, end_date]` and save to `filepath`.

### `DOYPlotter.plot(self)`

Render the default range/time residuals figure for all transponders.

### `DOYPlotter.set_df_merged_date(self, start: datetime.datetime, end: datetime.datetime)`

Restrict the active merged dataframe to the `[start, end]` window.


## class `DOYResult`

Single day-of-year GARPOS result: shotdata + parsed inversion JSON.
