from datetime import datetime
from typing import List

import networkx as nx
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy.spatial.distance import cosine


class TopicVisualizer:
    def __init__(self, results_df: pd.DataFrame, dates: List[datetime], doc_topics: np.ndarray):
        """
        Initialize the topic visualizer.

        Args:
            results_df: DataFrame containing topic analysis results
            dates: List of dates for each document
            doc_topics: Document-topic distribution matrix
        """
        self.results_df = results_df
        self.dates = dates
        self.doc_topics = doc_topics
        self.date_range = pd.date_range(min(dates), max(dates), freq='D')

    def plot_topic_evolution(self, top_n: int = 5) -> go.Figure:
        """
        Plot the evolution of topic prominence over time.

        Args:
            top_n: Number of top topics to display

        Returns:
            Plotly figure showing topic evolution
        """
        # Get top N topics by crowding score
        top_topics = self.results_df.nlargest(top_n, 'crowding_score')

        # Create daily topic prominence
        daily_prominence = pd.DataFrame(index=self.date_range)

        # Calculate daily average prominence for each topic
        for topic_id in top_topics['topic_id']:
            topic_probs = self.doc_topics[:, topic_id]
            daily_values = pd.Series(index=self.dates, data=topic_probs)
            daily_avg = daily_values.resample('D').mean()
            daily_prominence[f"Topic {topic_id}"] = daily_avg

        # Create figure
        fig = go.Figure()

        # Add line for each topic
        for topic_id, topic_data in top_topics.iterrows():
            keywords = ', '.join(topic_data['keywords'][:3])
            col_name = f"Topic {topic_data['topic_id']}"

            fig.add_trace(go.Scatter(
                x=daily_prominence.index,
                y=daily_prominence[col_name],
                name=f"Topic {topic_data['topic_id']}: {keywords}",
                mode='lines',
                line=dict(width=2),
                hovertemplate="Date: %{x}<br>Prominence: %{y:.2f}<extra></extra>"
            ))

        fig.update_layout(
            title="Topic Evolution Over Time",
            xaxis_title="Date",
            yaxis_title="Topic Prominence",
            hovermode='x unified',
            showlegend=True,
            template="plotly_white"
        )

        return fig

    def plot_crowding_heatmap(self) -> go.Figure:
        """
        Create a heatmap showing topic relationships and crowding.

        Returns:
            Plotly figure showing topic crowding heatmap
        """
        # Calculate topic similarity matrix
        n_topics = len(self.results_df)
        similarity_matrix = np.zeros((n_topics, n_topics))

        for i in range(n_topics):
            for j in range(n_topics):
                if i != j:
                    # Calculate cosine similarity between topic distributions
                    sim = 1 - cosine(self.doc_topics[:, i], self.doc_topics[:, j])
                    similarity_matrix[i, j] = sim

        # Create labels with keywords
        labels = [f"Topic {row['topic_id']}<br>({', '.join(row['keywords'][:2])})"
                  for _, row in self.results_df.iterrows()]

        # Create heatmap
        fig = go.Figure(data=go.Heatmap(
            z=similarity_matrix,
            x=labels,
            y=labels,
            colorscale='RdBu',
            zmid=0.5,
            text=np.round(similarity_matrix, 2),
            texttemplate="%{text}",
            textfont={"size": 10},
            hoverongaps=False,
            hovertemplate="Topic 1: %{y}<br>Topic 2: %{x}<br>Similarity: %{z:.2f}<extra></extra>"
        ))

        fig.update_layout(
            title="Topic Similarity Heatmap",
            xaxis_title="",
            yaxis_title="",
            xaxis={'side': 'bottom'},
            template="plotly_white",
            width=900,
            height=900
        )

        return fig

    def plot_topic_network(self, similarity_threshold: float = 0.3) -> go.Figure:
        """
        Create a network visualization of topic relationships.

        Args:
            similarity_threshold: Minimum similarity for topics to be connected

        Returns:
            Plotly figure showing topic network
        """
        # Create network graph
        G = nx.Graph()

        # Add nodes
        for _, row in self.results_df.iterrows():
            G.add_node(row['topic_id'],
                       size=row['crowding_score'] * 20,
                       keywords=', '.join(row['keywords'][:3]))

        # Add edges based on topic similarity
        for i, row1 in self.results_df.iterrows():
            for j, row2 in self.results_df.iterrows():
                if i < j:
                    sim = 1 - cosine(self.doc_topics[:, row1['topic_id']],
                                     self.doc_topics[:, row2['topic_id']])
                    if sim > similarity_threshold:
                        G.add_edge(row1['topic_id'], row2['topic_id'],
                                   weight=sim)

        # Calculate layout
        pos = nx.spring_layout(G)

        # Create figure
        fig = go.Figure()

        # Add edges
        edge_x = []
        edge_y = []
        for edge in G.edges():
            x0, y0 = pos[edge[0]]
            x1, y1 = pos[edge[1]]
            edge_x.extend([x0, x1, None])
            edge_y.extend([y0, y1, None])

        fig.add_trace(go.Scatter(
            x=edge_x, y=edge_y,
            line=dict(width=0.5, color='#888'),
            hoverinfo='none',
            mode='lines'))

        # Add nodes
        node_x = []
        node_y = []
        node_size = []
        node_text = []

        for node in G.nodes():
            x, y = pos[node]
            node_x.append(x)
            node_y.append(y)
            node_size.append(G.nodes[node]['size'])
            node_text.append(f"Topic {node}<br>{G.nodes[node]['keywords']}")

        fig.add_trace(go.Scatter(
            x=node_x, y=node_y,
            mode='markers',
            hoverinfo='text',
            text=node_text,
            marker=dict(
                showscale=True,
                colorscale='YlOrRd',
                size=node_size,
                color=[self.results_df[self.results_df['topic_id'] == n]['crowding_score'].iloc[0]
                       for n in G.nodes()],
                line_width=2)))

        fig.update_layout(
            title="Topic Relationship Network",
            showlegend=False,
            hovermode='closest',
            margin=dict(b=20, l=5, r=5, t=40),
            template="plotly_white",
            xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            yaxis=dict(showgrid=False, zeroline=False, showticklabels=False)
        )

        return fig

    def plot_metrics_dashboard(self) -> go.Figure:
        """
        Create a comprehensive dashboard of topic metrics.

        Returns:
            Plotly figure showing metrics dashboard
        """
        # Create subplot figure
        fig = make_subplots(
            rows=2, cols=2,
            subplot_titles=(
                "Topic Crowding Scores",
                "Metrics Distribution",
                "Temporal Patterns",
                "Topic Diversity vs Overlap"
            )
        )

        # 1. Topic Crowding Scores (Bar Chart)
        top_topics = self.results_df.nlargest(10, 'crowding_score')
        fig.add_trace(
            go.Bar(
                x=top_topics['crowding_score'],
                y=[f"Topic {tid}: {', '.join(kw[:2])}"
                   for tid, kw in zip(top_topics['topic_id'], top_topics['keywords'])],
                orientation='h',
                name="Crowding Score"
            ),
            row=1, col=1
        )

        # 2. Metrics Distribution (Box Plot)
        metrics = ['prominence_score', 'velocity', 'diversity_score', 'overlap_score']
        fig.add_trace(
            go.Box(
                x=metrics,
                y=[self.results_df[m] for m in metrics],
                name="Metrics Distribution"
            ),
            row=1, col=2
        )

        # 3. Temporal Patterns (Line Chart)
        daily_activity = pd.Series(self.dates).value_counts().sort_index()
        fig.add_trace(
            go.Scatter(
                x=daily_activity.index,
                y=daily_activity.values,
                mode='lines',
                name="Daily Activity"
            ),
            row=2, col=1
        )

        # 4. Diversity vs Overlap Scatter
        fig.add_trace(
            go.Scatter(
                x=self.results_df['diversity_score'],
                y=self.results_df['overlap_score'],
                mode='markers',
                text=[f"Topic {tid}: {', '.join(kw[:2])}"
                      for tid, kw in zip(self.results_df['topic_id'],
                                         self.results_df['keywords'])],
                marker=dict(
                    size=self.results_df['crowding_score'] * 20,
                    color=self.results_df['velocity'],
                    colorscale='Viridis',
                    showscale=True
                ),
                name="Topics"
            ),
            row=2, col=2
        )

        # Update layout
        fig.update_layout(
            height=800,
            title_text="Topic Analysis Dashboard",
            showlegend=False,
            template="plotly_white"
        )

        # Update axes labels
        fig.update_xaxes(title_text="Score", row=1, col=1)
        fig.update_xaxes(title_text="Metric", row=1, col=2)
        fig.update_xaxes(title_text="Date", row=2, col=1)
        fig.update_xaxes(title_text="Diversity Score", row=2, col=2)

        fig.update_yaxes(title_text="Topic", row=1, col=1)
        fig.update_yaxes(title_text="Value", row=1, col=2)
        fig.update_yaxes(title_text="Activity", row=2, col=1)
        fig.update_yaxes(title_text="Overlap Score", row=2, col=2)

        return fig
