import pandas as pd
import numpy as np
from xgboost import XGBClassifier, XGBRegressor
import shap
from sklearn.metrics import roc_auc_score, mean_squared_error
from utils import timeit, check_path, save_dataframe_to_csv
import gc
import os
import warnings
import logging.config
import logging
gc.enable()
warnings.filterwarnings('ignore')
logging.config.fileConfig(fname='logger.ini', defaults={'logfilename': 'logfile.log'})

class FeatureSelector(object):

  @timeit
  def __init__(self, parameters):
    if not isinstance(parameters, dict):
      raise TypeError('"parameters" must be a dict type')
    if 'path_data' not in parameters.keys():
      raise KeyError('"path_data" is not in "parameters", "path_data" is a necessary parameter')
    if not check_path(parameters['path_data']):
      raise ValueError(f"'{parameters['path_data']}' does not exists")
    if not parameters['target']:
      raise KeyError('"target" is not in "parameters", "target" is a necessary parameter')

    self.path = parameters['path_data']
    self.sep = parameters['sep']
    self.target = parameters['target']
    self.id = parameters['id']
    self.type = parameters['type']
    self.num_features = parameters['num_features']
    self.threshold = parameters['threshold']
    self.n_jobs = parameters['n_jobs']
    self.output_file_name = parameters['output_file_name']
    self.df = None

    logging.info(f"Object {self} is created")

  @timeit
  def __call__(self):
    """
    Combine and run all of class methods for selecting features
    """
    self.create_dataframes()
    self.get_feature_by_shap()
    self.one_factor_calculate_score()
    self.one_factor_selection()

  @timeit
  def create_dataframes(self):
    """
      Make DataFrame objects from csv file
    """
    self.df = pd.read_csv(self.path, sep=self.sep, encoding="utf-8", low_memory=False)

  def build_model(self, columns):
    """
      Build model on selected features
    """
    if self.type == 'classification':
      model = XGBClassifier( 
          objective='binary:logistic',
          booster='gbtree',
          three_method='gpu_hist',
          n_jobs=self.n_jobs,
          random_state=42,
          eval_metric='auc',
          early_stopping_rounds=5,
          verbosity = 0)
      
    if self.type == 'regression':
      model = XGBRegressor( 
          objective='reg:linear',
          booster='gbtree',
          three_method='gpu_hist',
          n_jobs=self.n_jobs,
          random_state=42,
          eval_metric='rmse',
          early_stopping_rounds=5,
          verbosity = 0)
      
    model.fit(self.df[columns], self.df[self.target], 
              eval_set=[(self.df[columns], self.df[self.target])], 
              verbose=False)
    return model

  def get_df_importance(self, value, columns):
    """
      Make DataFrame objects from importance values and feature names 
    """   
    df_importance = pd.DataFrame(list(zip(columns, value)), columns=['name', 'importance'])
    df_importance.sort_values(by = 'importance', ascending=False, inplace=True)
    df_importance = df_importance[df_importance['importance'] != 0]
    return list(df_importance['name'])

  @timeit
  def get_feature_by_importance(self):
    """
      Get feature importance by XGBoost importance 
    """   
    columns = list(self.df.columns)
    columns.remove(self.target)
    columns.remove(self.id)
    len_previous_columns = -1

    while len(columns) != len_previous_columns:
      logging.info(f"{len(columns)} was selected")
      len_previous_columns = len(columns)
      model = self.build_model(columns)
      columns = self.get_df_importance(model.feature_importances_, columns)
    if len(columns) > 1:
      self.df = self.df[[self.id] + columns + [self.target]]
    else:
      self.df = self.df[[self.id] + [columns] + [self.target]]

  @timeit
  def get_feature_by_shap(self):
    """
      Get feature importance by Shap importance 
    """
    columns = list(self.df.columns)
    columns.remove(self.target)
    columns.remove(self.id)
    len_previous_columns = -1

    while len(columns) != len_previous_columns:
      logging.info(f"{len(columns)} was selected")
      len_previous_columns = len(columns)
      model = self.build_model(columns)
      shap_values = shap.TreeExplainer(model).shap_values(self.df[columns])
      columns = self.get_df_importance(np.abs(shap_values).mean(0), columns)
    if len(columns) > 1:
      self.df = self.df[[self.id] + columns + [self.target]]
    else:
      self.df = self.df[[self.id] + [columns] + [self.target]]
      logging.info(f"Feature final size: {len(columns)}")

  @timeit
  def one_factor_calculate_score(self):
    """
      Calculate roc_auc score for each feature
    """
    list_score = []
    columns = list(self.df.columns)
    columns.remove(self.target)
    columns.remove(self.id)
    if len(columns) > 1:
      for column in columns:
        model = self.build_model([column])
        if self.type == 'classification':
          predict = model.predict_proba(self.df[[column]])[:, 1]
          score = roc_auc_score(self.df[self.target], predict)
        if self.type == 'regression':
          predict = model.predict(self.df[[column]])
          score = np.sqrt(mean_squared_error(self.df[self.target], predict))
        list_score.append(score)

      df_scores = pd.DataFrame(list(zip(columns, list_score)), columns=['name', 'score'])
      df_scores.sort_values(by = 'score', 
                            ascending=[False if self.type =='classification' else True][0], 
                            inplace=True)
      self.df = self.df[[self.id] + list(df_scores['name']) + [self.target]]
  
  @timeit
  def one_factor_selection(self):
    """
      Feature selection by element-wise removal of 
      the most unimportant (by roc_auc score) features
    """
    previous_score = -1.0
    current_score = 0.0
    columns = list(self.df.columns)
    columns.remove(self.target)
    columns.remove(self.id)

    if len(columns) > 1:
      model = self.build_model(columns)
      if self.type == 'classification':
        predict = model.predict_proba(self.df[columns])[:, 1]
        current_score = roc_auc_score(self.df[self.target], predict)
      if self.type == 'regression':
        predict = model.predict(self.df[columns])
        current_score = -np.sqrt(mean_squared_error(self.df[self.target], predict))

      while (previous_score - current_score <= self.threshold) and (len(columns) > 1):
        drop_column = columns.pop(-1)
        previous_score = current_score
        model = self.build_model(columns)
        if self.type == 'classification':
          predict = model.predict_proba(self.df[columns])[:, 1]
          current_score = roc_auc_score(self.df[self.target], predict)
        if self.type == 'regression':
          predict = model.predict(self.df[columns])
          current_score = -np.sqrt(mean_squared_error(self.df[self.target], predict))
        logging.info(f"{len(columns) + 1} was selected")
      columns = columns + [drop_column]

      if self.num_features != None:
        self.df = self.df[[self.id] + columns[:self.num_features] + [self.target]]
        logging.info(f"{self.num_features} was selected")
        logging.info(f"Feature final size: {self.num_features}")
      else:
        self.df = self.df[[self.id] + columns + [self.target]]
        logging.info(f"Feature final size: {len(columns)}")

    gc.collect()
    save_dataframe_to_csv(self.df, self.output_file_name, self.sep)