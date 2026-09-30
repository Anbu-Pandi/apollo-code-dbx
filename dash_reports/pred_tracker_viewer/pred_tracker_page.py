import dash
import dash_html_components as html
import dash_core_components as dcc
from dash.dependencies import Input, Output
import requests
import json

def create_pred_tracker_page():
    """Create a Dash page that embeds the pred_tracker data."""
    
    # Use relative URL - proxied through dash_reports
    # Fullscreen iframe with no padding/margins
    layout = html.Div([
        html.Iframe(
            src='/pred-tracker-api/',
            style={
                'width': '100vw',
                'height': '100vh',
                'border': 'none',
                'position': 'fixed',
                'top': '0',
                'left': '0',
                'margin': '0',
                'padding': '0'
            }
        )
    ], style={
        'margin': '0',
        'padding': '0',
        'width': '100%',
        'height': '100vh',
        'overflow': 'hidden',
        'position': 'relative'
    })
    
    return layout


def register_callbacks(app):
    """Register callbacks for the pred tracker page."""
    pass  # Iframe handles all interaction


# Page registration
layout = create_pred_tracker_page()
