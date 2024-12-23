"""
Copyright (c) 2024 Baltic Marine Environment Protection Commission
Copyright (c) 2022 Antti-Jussi Kieloaho (Natural Resources Institute Finland)

LICENSE available under 
local: 'SOM/protect_baltic/LICENSE'
url: 'https://github.com/helcomsecretariat/SOM/blob/main/protect_baltic/LICENCE'
"""

from copy import deepcopy

import numpy as np
import pandas as pd

from som.som_tools import *
from utilities import Timer, exception_traceback

def process_input_data(config: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Reads in data and processes to usable form.

    Arguments:
        config (dict): dictionary loaded from configuration file

    Returns:
        measure_survey_df (DataFrame): contains the measure survey data of expert panels
        pressure_survey_df (DataFrame): contains the pressure survey data of expert panels
        data (dict): 
            'measure' (DataFrame):
                'ID': unique measure identifier
                'measure': name / description column
            'activity' (DataFrame):
                'ID': unique activity identifier
                'activity': name / description column
            'pressure' (DataFrame):
                'ID': unique pressure identifier
                'pressure': name / description column
            'state' (DataFrame):
                'ID': unique state identifier
                'state': name / description column
            'measure_effects' (DataFrame): measure effects on activities / pressures / states
            'pressure_contributions' (DataFrame): pressure contributions to states
            'thresholds' (DataFrame): changes in states required to meet specific target thresholds
            'domain'
            'cases'
            'activity_contributions'
            'overlaps'
            'development_scenarios'
    """
    #
    # measure survey data
    #

    file_name = config['input_files']['measure_effect_input']
    measure_effects = process_measure_survey_data(file_name, config['measure_survey_sheets'])

    #
    # pressure survey data (combined pressure contributions and GES threshold)
    #

    file_name = config['input_files']['pressure_state_input']
    pressure_contributions, thresholds = process_pressure_survey_data(file_name, config['pressure_survey_sheets'])

    #
    # measure / pressure / activity / state links
    #

    # read core object descriptions
    # i.e. ids for measures, activities, pressures and states
    file_name = config['input_files']['general_input']
    id_sheets = config['general_input_sheets']['ID']
    data = read_ids(file_name=file_name, id_sheets=id_sheets)

    #
    # read case input
    #

    file_name = config['input_files']['general_input']
    sheet_name = config['general_input_sheets']['case']
    cases = read_cases(file_name=file_name, sheet_name=sheet_name)

    #
    # read activity contribution data
    #

    file_name = config['input_files']['general_input']
    sheet_name = config['general_input_sheets']['postprocess']
    activity_contributions = read_activity_contributions(file_name=file_name, sheet_name=sheet_name)

    #
    # read overlap data
    #

    file_name = config['input_files']['general_input']
    sheet_name = config['general_input_sheets']['overlaps']
    overlaps = read_overlaps(file_name=file_name, sheet_name=sheet_name)

    #
    # read activity development scenario data
    #

    file_name = config['input_files']['general_input']
    sheet_name = config['general_input_sheets']['development_scenarios']
    development_scenarios = read_development_scenarios(file_name=file_name, sheet_name=sheet_name)

    data.update({
        'measure_effects': measure_effects, 
        'pressure_contributions': pressure_contributions, 
        'thresholds': thresholds, 
        'cases': cases, 
        'activity_contributions': activity_contributions, 
        'overlaps': overlaps, 
        'development_scenarios': development_scenarios
    })

    data = link_area_ids(data)

    return data


def build_links(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """
    Builds links.

    Arguments:
        data (dict):

    Returns:
        links (DataFrame) = Measure-Activity-Pressure-State reduction table
    """
    msdf = data['measure_effects']

    # verify that there are no duplicate links
    assert len(msdf[msdf.duplicated(['measure', 'activity', 'pressure', 'state'])]) == 0

    # create a new dataframe for links
    links = pd.DataFrame(msdf)

    # get picks from cumulative distribution
    links['reduction'] = links['cumulative probability'].apply(get_pick)
    links = links.drop(columns=['cumulative probability'])

    # initialize multiplier column
    links['multiplier'] = np.ones(len(msdf))

    #
    # Overlaps (measure-measure interaction)
    #

    measure_ids = links['measure'].unique()
    overlaps = data['overlaps']
    for id in measure_ids:
        rows = overlaps.loc[overlaps['Overlapping'] == id, :]
        for i, row in rows.iterrows():
            overlapping_id = row['Overlapping']
            overlapped_id = row['Overlapped']
            pressure_id = row['Pressure']
            activity_id = row['Activity']
            multiplier = row['Multiplier']
            query = (links['measure'] == overlapped_id) & (links['pressure'] == pressure_id)
            if activity_id != 0:
                query = query & (links['activity'] == activity_id)
            links.loc[query, 'multiplier'] = links.loc[query, 'multiplier'] * multiplier

    return links


def build_scenario(data: dict[str, pd.DataFrame], scenario: str) -> pd.DataFrame:
    """
    Build scenario
    """
    act_to_press = data['activity_contributions']
    dev_scen = data['development_scenarios']

    # for each pressure, save the total contribution of activities for later normalization
    actual_sum = {}
    for pressure_id in act_to_press['Pressure'].unique():
        actual_sum[pressure_id] = {}
        activities = act_to_press.loc[act_to_press['Pressure'] == pressure_id, :]
        for area in activities['area_id'].unique():
            actual_sum[pressure_id][area] = activities.loc[activities['area_id'] == area, 'value'].sum()
    
    # multiply activities by scenario multiplier
    def get_scenario(activity_id):
        multiplier = dev_scen.loc[dev_scen['Activity'] == activity_id, scenario]
        if len(multiplier) == 0:
            return 1
        multiplier = multiplier.values[0]
        return multiplier
    act_to_press['value'] = act_to_press['value'] * act_to_press['Activity'].apply(get_scenario)

    # normalize
    normalize_factor = {}
    for pressure_id in act_to_press['Pressure'].unique():
        normalize_factor[pressure_id] = {}
        activities = act_to_press.loc[act_to_press['Pressure'] == pressure_id, :]
        for area in activities['area_id'].unique():
            scenario_sum = activities.loc[activities['area_id'] == area, 'value'].sum()
            normalize_factor[pressure_id][area] = 1 + scenario_sum - actual_sum[pressure_id][area]

    def normalize(value, pressure_id, area_id):
        return value * normalize_factor[pressure_id][area_id]

    act_to_press['value'] = act_to_press.apply(lambda x: normalize(x['value'], x['Pressure'], x['area_id']), axis=1)
    
    return act_to_press


def build_cases(cases: pd.DataFrame, links: pd.DataFrame) -> pd.DataFrame:
    """
    Builds cases.
    """
    # replace all zeros (0) in activity / pressure / state columns with full list of values
    # filter those lists to only include relevant IDs (from links)
    # finally explode to only have single IDs per row
    cols = ['activity', 'pressure', 'state']
    for col in cols:
        cases[col] = cases[col].astype(object)
    for i, row in cases.iterrows():
        maps_links = links.loc[links['measure'] == row['measure'], cols]    # select relevant measure/activity/pressure/state links
        if len(maps_links) == 0:
            cases.drop(i, inplace=True) # drop rows where measure has no effect
            continue
        for col in cols:
            cases.at[i, col] = maps_links[col].unique().tolist() if row[col] == 0 else row[col]
    for col in cols:
        cases = cases.explode(col)
    
    cases = cases.reset_index(drop=True)

    # filter out links that don't have associated reduction
    m = cases['measure'].isin(links['measure'])
    a = cases['activity'].isin(links['activity'])
    p = cases['pressure'].isin(links['pressure'])
    s = cases['state'].isin(links['state'])
    existing_links = (m & a & p & s)
    cases = cases.loc[existing_links, :]

    cases = cases.reset_index(drop=True)

    return cases


def build_changes(data: dict[str, pd.DataFrame], links: pd.DataFrame) -> pd.DataFrame:
    """
    Simulate the reduction in activities and pressures caused by measures and 
    return the change observed in state. 
    """
    cases = data['cases']
    areas = cases['area_id'].unique()

    # create new dataframes for pressures and states each, one column per area_id
    # the value of each cell is the reduction in the pressure for that area
    # NOTE: the DataFrames are created on one line to avoid PerformanceWarning
    pressure_change = pd.DataFrame(data['pressure']['ID']).reindex(columns=['ID']+areas.tolist()).fillna(1.0)
    state_change = pd.DataFrame(data['state']['ID']).reindex(columns=['ID']+areas.tolist()).fillna(1.0)

    # TODO: add development over time here
    # so that it multiplies with the pressure change


    # TODO: for each area
    # for each pressure
    # for each measure from cases
    # subtract activity-pressure reduction from links (multiplied with dev and press. contribution)

    # go through each case individually
    for area in areas:
        c = cases.loc[cases['area_id'] == area, :]  # select cases for current area
        for p_i, p in pressure_change.iterrows():
            relevant_measures = c.loc[c['pressure'] == p['ID'], :]
            for m_i, m in relevant_measures.iterrows(): # for each case of the current pressure in the current area
                mask = (links['measure'] == m['measure']) & (links['activity'] == m['activity']) & (links['pressure'] == m['pressure']) & (links['state'] == m['state'])
                row = links.loc[mask, :]
                if len(row) == 0:
                    continue
                else:
                    red = row['reduction'].values[0]
                    multiplier = row['multiplier'].values[0]
                for mod in ['coverage', 'implementation']:
                    multiplier = multiplier * m[mod]
                reduction = red * multiplier
                # if activity is 0 (= straight to pressure), contribution will be 1
                if m['activity'] == 0:
                    contribution = 1
                # if activity is not in contribution list, contribution will be 0
                mask = (data['activity_contributions']['Activity'] == m['activity']) & (data['activity_contributions']['Pressure'] == m['pressure']) & (data['activity_contributions']['area_id'] == area)
                contribution = data['activity_contributions'].loc[mask, 'value']
                if len(contribution) == 0:
                    contribution = 0
                else:
                    contribution = contribution.values[0]
                pressure_change.at[p_i, area] = pressure_change.at[p_i, area] - reduction * contribution

    # TODO: for each area
    # for each state
    # for each measure from cases
    # subtract state reduction
    # also, subtract reduction seen in pressure multiplied with corresponding pressure contribution

    # straight to state measures
    for area in areas:
        c = cases.loc[cases['area_id'] == area, :]
        for s_i, s in state_change.iterrows():
            relevant_measures = c.loc[c['state'] == s['ID'], :]
            for m_i, m in relevant_measures.iterrows():
                mask = (links['measure'] == m['measure']) & (links['activity'] == m['activity']) & (links['pressure'] == m['pressure']) & (links['state'] == m['state'])
                row = links.loc[mask, :]
                if len(row) == 0:
                    continue
                else:
                    red = row['reduction'].values[0]
                    multiplier = row['multiplier'].values[0]
                for mod in ['coverage', 'implementation']:
                    multiplier = multiplier * m[mod]
                reduction = red * multiplier
                state_change.at[s_i, area] = state_change.at[s_i, area] - reduction
    
    # pressure contributions
    pressure_contributions = data['pressure_contributions']
    for area in areas:
        a_i = pressure_change.columns.get_loc(area)
        for s_i, s in state_change.iterrows():
            relevant_pressures = pressure_contributions.loc[pressure_contributions['State'] == s['ID'], :]
            for p_i, p in relevant_pressures.iterrows():
                row_i = pressure_change.loc[pressure_change['ID'] == p['pressure']].index[0]
                reduction = pressure_change.iloc[row_i, a_i]
                contribution = p['average']
                state_change.at[s_i, area] = state_change.at[s_i, area] - contribution * reduction
    
    # compare state reduction to GES threshold
    thresholds = data['thresholds']
    cols = ['PR', '10', '25', '50']
    state_ges = {}
    for col in cols:
        state_ges[col] = pd.DataFrame(data['state']['ID']).reindex(columns=['ID']+areas.tolist()).fillna(1.0)
    for area in areas:
        a_i = state_change.columns.get_loc(area)
        for s_i, s in state_change.iterrows():
            row = thresholds.loc[(thresholds['State'] == s['ID']) & (thresholds['area_id'] == area), cols]
            if len(row) == 0:
                continue
            for col in cols:
                state_ges[col].iloc[s_i, a_i] = row.loc[:, col].values[0]

    data.update({
        'pressure_change': pressure_change, 
        'state_change': state_change, 
        'state_ges': state_ges
    })

    return data


#EOF