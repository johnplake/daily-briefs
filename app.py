"""
Dash + Plotly prototype for interactive embeddings visualization.

Features:
- 2D scatter plot of paper embeddings
- Zoom, pan, lasso select
- Filter by stream/date
- Hover to see paper details
- Click to show full paper info in sidebar
"""

import dash
from dash import dcc, html, Input, Output, State
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import numpy as np
from pathlib import Path
import json

# Load embeddings and paper metadata
# (You'll need to generate these - see generate_demo_data() below)
DATA_FILE = Path("data/embeddings_2d.json")

def load_data():
    """Load embeddings and metadata."""
    if not DATA_FILE.exists():
        # Generate demo data if file doesn't exist
        return generate_demo_data()
    
    with open(DATA_FILE) as f:
        data = json.load(f)
    return pd.DataFrame(data)

def generate_demo_data():
    """Generate synthetic demo data."""
    np.random.seed(42)
    n = 300
    
    streams = np.random.choice(['Popular', 'Interest', 'Serendipity'], n)
    dates = pd.date_range('2026-01-01', periods=30, freq='D')
    
    # Cluster embeddings by stream
    data = []
    for i in range(n):
        stream = streams[i]
        # Create clusters for each stream
        if stream == 'Popular':
            x = np.random.normal(0, 0.5)
            y = np.random.normal(0, 0.5)
        elif stream == 'Interest':
            x = np.random.normal(2, 0.4)
            y = np.random.normal(1, 0.4)
        else:  # Serendipity
            x = np.random.normal(-1, 0.6)
            y = np.random.normal(2, 0.6)
        
        data.append({
            'x': x,
            'y': y,
            'stream': stream,
            'date': str(np.random.choice(dates)),
            'title': f"Paper {i+1}: {stream} Research Topic",
            'authors': f"Author {i%50+1} et al.",
            'arxiv_id': f"2601.{i:05d}",
            'categories': np.random.choice(['cs.AI', 'cs.LG', 'cs.CL', 'stat.ML']),
            'score': np.random.uniform(0.5, 1.0),
        })
    
    return pd.DataFrame(data)

# Initialize data
df = load_data()

# Dash app
app = dash.Dash(__name__)

app.layout = html.Div([
    html.Div([
        html.H1("ArXiv Paper Embeddings Explorer", style={'textAlign': 'center'}),
        
        # Filters
        html.Div([
            html.Label("Filter by Stream:"),
            dcc.Dropdown(
                id='stream-filter',
                options=[{'label': 'All', 'value': 'all'}] + 
                        [{'label': s, 'value': s} for s in df['stream'].unique()],
                value='all',
                clearable=False,
                style={'width': '200px', 'display': 'inline-block', 'marginRight': '20px'}
            ),
            
            html.Label("Filter by Date:"),
            dcc.DatePickerRange(
                id='date-filter',
                start_date=df['date'].min(),
                end_date=df['date'].max(),
                style={'display': 'inline-block'}
            ),
        ], style={'padding': '20px', 'backgroundColor': '#f5f5f5'}),
        
        # Main content: scatter plot + sidebar
        html.Div([
            # Scatter plot
            html.Div([
                dcc.Graph(
                    id='scatter-plot',
                    style={'height': '700px'},
                    config={'displayModeBar': True}
                )
            ], style={'width': '70%', 'display': 'inline-block', 'verticalAlign': 'top'}),
            
            # Sidebar for paper details
            html.Div([
                html.H3("Paper Details", style={'borderBottom': '2px solid #333'}),
                html.Div(id='paper-details', style={
                    'padding': '20px',
                    'backgroundColor': '#fafafa',
                    'borderRadius': '5px',
                    'minHeight': '600px'
                })
            ], style={
                'width': '28%',
                'display': 'inline-block',
                'verticalAlign': 'top',
                'marginLeft': '2%',
                'padding': '20px',
                'backgroundColor': '#fff',
                'border': '1px solid #ddd',
                'borderRadius': '5px'
            })
        ])
    ], style={'padding': '20px'})
])

@app.callback(
    Output('scatter-plot', 'figure'),
    Input('stream-filter', 'value'),
    Input('date-filter', 'start_date'),
    Input('date-filter', 'end_date')
)
def update_scatter(stream, start_date, end_date):
    """Update scatter plot based on filters."""
    filtered_df = df.copy()
    
    # Filter by stream
    if stream != 'all':
        filtered_df = filtered_df[filtered_df['stream'] == stream]
    
    # Filter by date
    if start_date and end_date:
        filtered_df = filtered_df[
            (filtered_df['date'] >= start_date) &
            (filtered_df['date'] <= end_date)
        ]
    
    # Create scatter plot
    fig = px.scatter(
        filtered_df,
        x='x',
        y='y',
        color='stream',
        hover_data=['title', 'authors', 'arxiv_id', 'date', 'score'],
        color_discrete_map={
            'Popular': '#FF6B6B',
            'Interest': '#4ECDC4',
            'Serendipity': '#95E1D3'
        },
        title=f"Showing {len(filtered_df)} papers"
    )
    
    fig.update_traces(
        marker=dict(size=10, opacity=0.7, line=dict(width=0.5, color='white')),
        hovertemplate='<b>%{customdata[0]}</b><br>' +
                      'Authors: %{customdata[1]}<br>' +
                      'arXiv: %{customdata[2]}<br>' +
                      'Date: %{customdata[3]}<br>' +
                      'Score: %{customdata[4]:.2f}<br>' +
                      '<extra></extra>'
    )
    
    fig.update_layout(
        xaxis_title="Dimension 1",
        yaxis_title="Dimension 2",
        hovermode='closest',
        plot_bgcolor='#f8f9fa',
        paper_bgcolor='white',
    )
    
    return fig

@app.callback(
    Output('paper-details', 'children'),
    Input('scatter-plot', 'clickData'),
    prevent_initial_call=True
)
def display_paper_details(clickData):
    """Show detailed paper info when point is clicked."""
    if not clickData:
        return html.P("Click on a point to see paper details")
    
    point = clickData['points'][0]
    # customdata order: title, authors, arxiv_id, date, score
    title = point['customdata'][0]
    authors = point['customdata'][1]
    arxiv_id = point['customdata'][2]
    date = point['customdata'][3]
    score = point['customdata'][4]
    stream = point['marker.color']  # This will be the stream name
    
    # Get the full row from dataframe
    idx = point['pointIndex']
    row = df.iloc[idx]
    
    return html.Div([
        html.H4(title, style={'color': '#2c3e50', 'marginBottom': '15px'}),
        
        html.P([
            html.Strong("Authors: "),
            html.Span(authors)
        ]),
        
        html.P([
            html.Strong("arXiv ID: "),
            html.A(arxiv_id, href=f"https://arxiv.org/abs/{arxiv_id}", target="_blank")
        ]),
        
        html.P([
            html.Strong("Date: "),
            html.Span(date)
        ]),
        
        html.P([
            html.Strong("Stream: "),
            html.Span(row['stream'], style={
                'padding': '2px 8px',
                'backgroundColor': {'Popular': '#FF6B6B', 'Interest': '#4ECDC4', 'Serendipity': '#95E1D3'}[row['stream']],
                'color': 'white',
                'borderRadius': '3px'
            })
        ]),
        
        html.P([
            html.Strong("Categories: "),
            html.Span(row['categories'])
        ]),
        
        html.P([
            html.Strong("Score: "),
            html.Span(f"{score:.3f}")
        ]),
        
        html.Hr(),
        
        html.P([
            html.Strong("Abstract:"),
            html.Br(),
            html.I("(Abstract would go here - integrate with your paper metadata)")
        ], style={'fontSize': '14px', 'color': '#555'})
    ])

if __name__ == '__main__':
    print(f"Loaded {len(df)} papers")
    print(f"Streams: {df['stream'].value_counts().to_dict()}")
    app.run_server(debug=True, host='0.0.0.0', port=8050)
