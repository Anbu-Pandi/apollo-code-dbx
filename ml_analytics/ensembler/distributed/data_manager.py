import logging
import traceback
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd
from pyspark.sql import DataFrame as SparkDataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import DoubleType, StringType, TimestampType, StructType, StructField
from pyspark.sql.window import Window

from data_access.spark.api import sparkmanager
from ml_analytics.ensembler.utils import ReturnType
from ml_analytics.ensembler.data_manager import DataManager  # Import original data manager


class DistributedDataManager:
    """
    Distributed data manager that leverages your original pandas data loading
    and converts to Spark DataFrames for distributed processing
    """
    
    def __init__(self, features: List[str], return_type: Dict[str, ReturnType], 
                 window_days: float = 365, periodicity: str = 'DAILY', 
                 id_column: Optional[str] = None, num_partitions: int = 20):
        
        self.features = features
        self.return_type = return_type
        self.window_days = window_days
        self.periodicity = periodicity
        self.id_column = id_column
        self.num_partitions = num_partitions
        
        # Initialize Spark session
        if not hasattr(sparkmanager, '_spark') or sparkmanager._spark is None:
            try:
                sparkmanager.init()
            except Exception as e:
                logging.warning(f"Failed to initialize Spark: {str(e)}")
        
        self.spark = sparkmanager._spark
        
        # Create original DataManager instance to handle pandas data loading
        logging.info("Creating original DataManager for pandas data loading")
        self.pandas_data_manager = DataManager(
            features=features,
            return_type=return_type,
            window_days=window_days,
            periodicity=periodicity,
            id_column=id_column
        )
        
        # Cache for data
        self._spark_data_cache = None
        self._last_update = None

    def load_data(self, start_date: str = None, end_date: str = None, 
                  force_update: bool = False) -> SparkDataFrame:
        """
        Load data using original pandas approach and convert to Spark DataFrame
        """
        try:
            logging.info("Loading data using original pandas DataManager")
            
            # Check if we need to update data
            if force_update or self._spark_data_cache is None or self._should_update_data():
                # Use original data manager to get fresh data
                self.pandas_data_manager.update_data()
                
                # Get pandas DataFrame from original data manager
                pandas_df = self.pandas_data_manager.data_buffer.reset_index()
                
                # Filter by date range if provided
                if start_date or end_date:
                    pandas_df = self._filter_pandas_by_date(pandas_df, start_date, end_date)
                
                logging.info(f"Loaded {len(pandas_df)} rows from original DataManager")
                
                # Convert to Spark DataFrame
                if self.spark is not None:
                    spark_df = self._convert_pandas_to_spark(pandas_df)
                    self._spark_data_cache = spark_df
                    self._last_update = datetime.now()
                    
                    logging.info(f"Converted to Spark DataFrame with {spark_df.count()} rows")
                    return spark_df
                else:
                    raise RuntimeError("Spark session not available for distributed processing")
            else:
                logging.info("Using cached Spark DataFrame")
                return self._spark_data_cache
                
        except Exception as e:
            logging.error(f"Error loading data: {str(e)}")
            logging.error(traceback.format_exc())
            raise

    def _should_update_data(self) -> bool:
        """Check if data should be updated based on last update time"""
        if self._last_update is None:
            return True
        
        # Update if data is older than 1 hour (configurable)
        time_since_update = datetime.now() - self._last_update
        return time_since_update > timedelta(hours=1)

    def _filter_pandas_by_date(self, df: pd.DataFrame, start_date: str = None, 
                              end_date: str = None) -> pd.DataFrame:
        """Filter pandas DataFrame by date range"""
        try:
            if 'asof_date' not in df.columns:
                logging.warning("No asof_date column found for date filtering")
                return df
            
            # Ensure asof_date is datetime
            if not pd.api.types.is_datetime64_any_dtype(df['asof_date']):
                df['asof_date'] = pd.to_datetime(df['asof_date'])
            
            # Apply date filters
            if start_date:
                start_dt = pd.to_datetime(start_date)
                df = df[df['asof_date'] >= start_dt]
                
            if end_date:
                end_dt = pd.to_datetime(end_date)
                df = df[df['asof_date'] <= end_dt]
            
            logging.info(f"Date filtering: {len(df)} rows between {start_date} and {end_date}")
            return df
            
        except Exception as e:
            logging.error(f"Error filtering by date: {str(e)}")
            return df

    def _get_partition_info(self, df: SparkDataFrame) -> str:
        """Get partition information in a way that works with Databricks Connect"""
        try:
            # Try using explain to get partition info
            plan = df.explain(mode='formatted')
            return f"partitioned with {self.num_partitions} requested partitions"
        except:
            # Fallback to simple description
            return f"partitioned with {self.num_partitions} requested partitions"

    def _convert_pandas_to_spark(self, pandas_df: pd.DataFrame) -> SparkDataFrame:
        """Convert pandas DataFrame to Spark DataFrame with proper schema"""
        try:
            if pandas_df.empty:
                logging.warning("Empty pandas DataFrame, creating empty Spark DataFrame")
                return self._create_empty_spark_df()
            
            # Clean up pandas DataFrame for Spark conversion
            cleaned_df = self._prepare_pandas_for_spark(pandas_df)
            
            # Create Spark DataFrame
            logging.info("Converting pandas DataFrame to Spark DataFrame")
            spark_df = self.spark.createDataFrame(cleaned_df)
            
            # Repartition for optimal distributed processing
            if self.num_partitions > 0:
                if '_group' in spark_df.columns:
                    spark_df = spark_df.repartition(self.num_partitions, '_group', 'asof_date')
                else:
                    spark_df = spark_df.repartition(self.num_partitions, 'asof_date')
            
            # Cache for performance
            spark_df = spark_df.cache()
            
            partition_info = self._get_partition_info(spark_df)
            logging.info(f"Successfully converted to Spark DataFrame - {partition_info}")
            return spark_df
            
        except Exception as e:
            logging.error(f"Error converting pandas to Spark: {str(e)}")
            raise

    def _prepare_pandas_for_spark(self, df: pd.DataFrame) -> pd.DataFrame:
        """Prepare pandas DataFrame for Spark conversion"""
        # Make a copy to avoid modifying original
        cleaned_df = df.copy()
        
        # Handle missing values
        cleaned_df = cleaned_df.fillna(0.0)  # Replace NaN with 0
        
        # Ensure proper data types
        for col in cleaned_df.columns:
            if col == 'asof_date':
                # Ensure datetime
                cleaned_df[col] = pd.to_datetime(cleaned_df[col])
            elif col in ['_group'] + ([self.id_column] if self.id_column else []):
                # Ensure string
                cleaned_df[col] = cleaned_df[col].astype(str)
            elif col in self.features:
                # Ensure numeric for feature columns
                cleaned_df[col] = pd.to_numeric(cleaned_df[col], errors='coerce').fillna(0.0)
        
        # Add _group column if not exists
        if '_group' not in cleaned_df.columns:
            cleaned_df['_group'] = 'default'
        
        logging.info(f"Prepared pandas DataFrame: {cleaned_df.shape} with columns: {list(cleaned_df.columns)}")
        return cleaned_df

    def _create_empty_spark_df(self) -> SparkDataFrame:
        """Create empty Spark DataFrame with proper schema"""
        schema = StructType([
            StructField("asof_date", TimestampType(), True),
            StructField("_group", StringType(), True)
        ])
        
        # Add feature columns
        for feature in self.features:
            schema.add(StructField(feature, DoubleType(), True))
        
        return self.spark.createDataFrame([], schema)

    def get_return_type(self, ticker: str) -> ReturnType:
        """Get return type for a ticker (delegate to pandas data manager)"""
        return self.pandas_data_manager.get_return_type(ticker)

    def calculate_returns_spark(self, spark_df: SparkDataFrame) -> SparkDataFrame:
        """Calculate returns using Spark DataFrame operations"""
        try:
            logging.info("Calculating returns using Spark operations")
            
            # Create window specification for return calculations
            window_spec = Window.partitionBy("_group").orderBy("asof_date")
            
            # Calculate returns for each feature
            for feature in self.features:
                return_type = self.get_return_type(feature)
                
                if return_type == ReturnType.PCT_CHANGE:
                    spark_df = spark_df.withColumn(
                        f"{feature}_return",
                        (F.col(feature) / F.lag(feature, 1).over(window_spec) - 1.0)
                    )
                elif return_type == ReturnType.PRICE_DIFF:
                    spark_df = spark_df.withColumn(
                        f"{feature}_return",
                        F.col(feature) - F.lag(feature, 1).over(window_spec)
                    )
                elif return_type == ReturnType.LOG_RETURN:
                    spark_df = spark_df.withColumn(
                        f"{feature}_return",
                        F.log(F.col(feature) / F.lag(feature, 1).over(window_spec))
                    )
                else:  # ReturnType.LEVEL
                    spark_df = spark_df.withColumn(f"{feature}_return", F.col(feature))
            
            # Remove null values from return calculations
            spark_df = spark_df.dropna()
            
            logging.info("Return calculations completed")
            return spark_df
            
        except Exception as e:
            logging.error(f"Error calculating returns: {str(e)}")
            raise

    def prepare_features_for_ml(self, df: SparkDataFrame, target_feature: str, 
                               feature_list: List[str]) -> SparkDataFrame:
        """Prepare features for ML training with lag features"""
        try:
            group_cols = [self.id_column] if self.id_column else ['_group']
            window_spec = Window.partitionBy(*group_cols).orderBy('asof_date')
            
            # Create lag features
            for lag in range(1, 6):  # 5 lags
                for feature in feature_list:
                    if feature in df.columns:
                        df = df.withColumn(
                            f"{feature}_lag_{lag}",
                            F.lag(feature, lag).over(window_spec)
                        )
            
            # Create target variable (next period's target)
            target_col = f"{target_feature}_return" if f"{target_feature}_return" in df.columns else target_feature
            df = df.withColumn(
                "target",
                F.lead(target_col, 1).over(window_spec)
            )
            
            # Drop rows with nulls (incomplete lag features)
            df = df.dropna()
            
            return df
            
        except Exception as e:
            logging.error(f"Error preparing features: {str(e)}")
            return df

    def save_results(self, df: SparkDataFrame, output_table: str, 
                     output_schema: str = "tables/dbo", mode: str = "append") -> None:
        """Save results to Delta table"""
        try:
            # Write to Delta table using sparkmanager
            sparkmanager.writefile(
                df, 
                output_table, 
                output_schema, 
                partitioncolumn='asof_date',
                type='delta', 
                overwrite=(mode == "overwrite")
            )
            
            logging.info(f"Saved {df.count()} rows to {output_schema}.{output_table}")
            
        except Exception as e:
            logging.error(f"Error saving results: {str(e)}")
            raise

    def get_latest_data_window(self, window_size: int = 100) -> SparkDataFrame:
        """Get the latest window of data for real-time processing"""
        try:
            df = sparkmanager.readfile(
                self.pandas_data_manager.data_source_table, 
                self.pandas_data_manager.data_source_schema, 
                type='delta'
            )
            
            # Get latest dates
            latest_date = df.select(F.max('asof_date')).collect()[0][0]
            
            df = df.filter(F.col('asof_date') >= latest_date - timedelta(days=window_size))
            
            return self.calculate_returns_spark(df)
            
        except Exception as e:
            logging.error(f"Error getting latest data: {str(e)}")
            raise

    def convert_to_pandas_sample(self, df: SparkDataFrame, sample_size: int = 1000) -> pd.DataFrame:
        """Convert a sample of Spark DataFrame to pandas for local processing"""
        try:
            # Take a sample for local processing
            sample_df = df.sample(False, min(1.0, sample_size / df.count()))
            return sample_df.toPandas()
            
        except Exception as e:
            logging.error(f"Error converting to pandas: {str(e)}")
            return pd.DataFrame() 

    def update_data_from_source(self, force_refresh: bool = False):
        """Update data from original source and refresh Spark cache"""
        try:
            logging.info("Updating data from original source")
            
            if force_refresh:
                # Clear pandas data manager cache
                self.pandas_data_manager.data_buffer = pd.DataFrame()
            
            # Update using original data manager
            self.pandas_data_manager.update_data()
            
            # Clear Spark cache to force refresh
            self._spark_data_cache = None
            self._last_update = None
            
            logging.info("Data source update completed")
            
        except Exception as e:
            logging.error(f"Error updating data from source: {str(e)}")
            raise

    def get_data_summary(self) -> Dict:
        """Get summary statistics of the current data"""
        try:
            if self._spark_data_cache is None:
                return {"status": "No data loaded"}
            
            df = self._spark_data_cache
            
            summary = {
                "total_rows": df.count(),
                "requested_partitions": self.num_partitions,
                "date_range": {
                    "min_date": df.agg(F.min("asof_date")).collect()[0][0],
                    "max_date": df.agg(F.max("asof_date")).collect()[0][0]
                },
                "features": self.features,
                "groups": df.select("_group").distinct().count(),
                "last_update": self._last_update.isoformat() if self._last_update else None
            }
            
            return summary
            
        except Exception as e:
            logging.error(f"Error getting data summary: {str(e)}")
            return {"error": str(e)}

    def refresh_cache(self):
        """Refresh Spark DataFrame cache"""
        if self._spark_data_cache is not None:
            self._spark_data_cache.unpersist()
            self._spark_data_cache = None
            logging.info("Spark DataFrame cache cleared") 