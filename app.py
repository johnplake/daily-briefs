"""
Dash + Plotly dashboard for daily-briefs paper exploration.

Features:
- 2D UMAP scatter plot of all papers
- Search (full-text and semantic)
- Filter by category, date, stream
- Click paper → show full details
- Mobile-responsive layout
"""

import dash
from dash import dcc, html, Input, Output, State, ctx
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import sqlite3
from pathlib import Path

# Project paths
PROJECT_ROOT = Path(__file__).parent
DB_PATH = PROJECT_ROOT / "data" / "papers.db"

def get_db_connection():
    """Get database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def load_papers():
    """Load all papers with UMAP coordinates."""
    conn = get_db_connection()
    
    query = """
        SELECT 
            id, paper_id, title, abstract, authors,
            primary_category, categories, announced_date,
            arxiv_url, pdf_url,
            citations_s2, citations_oa,
            umap_x, umap_y
        FROM papers
        WHERE umap_x IS NOT NULL AND umap_y IS NOT NULL
        ORDER BY announced_date DESC
    """
    
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    # Parse authors JSON
    import json
    df['authors'] = df['authors'].apply(lambda x: ', '.join(json.loads(x)[:3]) if x else 'Unknown')
    
    return df

def search_papers(query_text):
    """Full-text search on papers."""
    if not query_text or query_text.strip() == "":
        return load_papers()
    
    conn = get_db_connection()
    
    search_query = """
        SELECT 
            p.id, p.paper_id, p.title, p.abstract, p.authors,
            p.primary_category, p.categories, p.announced_date,
            p.arxiv_url, p.pdf_url,
            p.citations_s2, p.citations_oa,
            p.umap_x, p.umap_y
        FROM papers p
        JOIN papers_fts fts ON p.id = fts.rowid
        WHERE papers_fts MATCH ?
          AND p.umap_x IS NOT NULL
        ORDER BY bm25(papers_fts)
        LIMIT 500
    """
    
    df = pd.read_sql_query(search_query, conn, params=(query_text,))
    conn.close()
    
    # Parse authors JSON
    import json
    df['authors'] = df['authors'].apply(lambda x: ', '.join(json.loads(x)[:3]) if x else 'Unknown')
    
    return df

# Initialize app
app = dash.Dash(__name__, suppress_callback_exceptions=True)

# Load initial data
df = load_papers()

# Get unique categories for filter
all_categories = set()
for cats in df['categories'].dropna():
    all_categories.update(cats.split())
category_options = [{'label': cat, 'value': cat} for cat in sorted(all_categories)]

app.layout = html.Div([
    # Header
    html.Div([
        html.H1("Daily Briefs Explorer", style={
            'textAlign': 'center',
            'color': '#2c3e50',
            'marginBottom': '10px'
        }),
        html.P(f"Visualizing {len(df)} papers with semantic embeddings", style={
            'textAlign': 'center',
            'color': '#7f8c8d'
        })
    ], style={'padding': '20px', 'backgroundColor': '#ecf0f1'}),
    
    # Search and filters
    html.Div([
        # Search box
        html.Div([
            dcc.Input(
                id='search-box',
                type='text',
                placeholder='Search papers (title, abstract, arXiv ID)...',
                style={
                    'width': '100%',
                    'padding': '10px',
                    'fontSize': '16px',
                    'borderRadius': '5px',
                    'border': '1px solid #bdc3c7'
                },
                debounce=True
            )
        ], style={'marginBottom': '15px'}),
        
        # Filters row
        html.Div([
            # Category filter
            html.Div([
                html.Label("Category:", style={'fontWeight': 'bold', 'marginBottom': '5px'}),
                dcc.Dropdown(
                    id='category-filter',
                    options=[{'label': 'All', 'value': 'all'}] + category_options,
                    value='all',
                    clearable=False,
                    style={'fontSize': '14px'}
                )
            ], style={'width': '30%', 'display': 'inline-block', 'marginRight': '3%'}),
            
            # Date range
            html.Div([
                html.Label("Date Range:", style={'fontWeight': 'bold', 'marginBottom': '5px'}),
                dcc.DatePickerRange(
                    id='date-filter',
                    start_date=df['announced_date'].min(),
                    end_date=df['announced_date'].max(),
                    style={'fontSize': '14px'}
                )
            ], style={'width': '66%', 'display': 'inline-block'})
        ], style={'marginBottom': '10px'})
    ], style={
        'padding': '20px',
        'backgroundColor': '#ffffff',
        'borderRadius': '5px',
        'boxShadow': '0 2px 4px rgba(0,0,0,0.1)',
        'margin': '20px'
    }),
    
    # Main content area
    html.Div([
        # Left: Scatter plot
        html.Div([
            dcc.Graph(
                id='scatter-plot',
                style={'height': '700px'},
                config={
                    'displayModeBar': True,
                    'displaylogo': False,
                    'modeBarButtonsToRemove': ['lasso2d']
                }
            )
        ], style={
            'width': '65%',
            'display': 'inline-block',
            'verticalAlign': 'top'
        }),
        
        # Right: Paper details sidebar
        html.Div([
            html.Div(id='paper-details', style={
                'padding': '20px',
                'backgroundColor': '#ffffff',
                'borderRadius': '5px',
                'minHeight': '650px',
                'boxShadow': '0 2px 4px rgba(0,0,0,0.1)'
            })
        ], style={
            'width': '33%',
            'display': 'inline-block',
            'verticalAlign': 'top',
            'paddingLeft': '2%'
        })
    ], style={'padding': '0 20px'}),
    
    # Store for filtered dataframe
    dcc.Store(id='filtered-data-store')
], style={'fontFamily': 'Arial, sans-serif', 'backgroundColor': '#f5f6fa'})

@app.callback(
    [Output('scatter-plot', 'figure'),
     Output('filtered-data-store', 'data')],
    [Input('search-box', 'value'),
     Input('category-filter', 'value'),
     Input('date-filter', 'start_date'),
     Input('date-filter', 'end_date')]
)
def update_scatter(search_query, category, start_date, end_date):
    """Update scatter plot based on search and filters."""
    
    # Load data (with search if provided)
    if search_query and search_query.strip():
        filtered_df = search_papers(search_query)
    else:
        filtered_df = load_papers()
    
    # Apply category filter
    if category != 'all' and not filtered_df.empty:
        filtered_df = filtered_df[filtered_df['categories'].str.contains(category, na=False)]
    
    # Apply date filter
    if start_date and end_date and not filtered_df.empty:
        filtered_df = filtered_df[
            (filtered_df['announced_date'] >= start_date) &
            (filtered_df['announced_date'] <= end_date)
        ]
    
    if filtered_df.empty:
        # Empty plot
        fig = go.Figure()
        fig.add_annotation(
            text="No papers match your filters",
            xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=20, color="gray")
        )
        return fig, None
    
    # Create scatter plot
    fig = px.scatter(
        filtered_df,
        x='umap_x',
        y='umap_y',
        color='primary_category',
        hover_data={
            'title': True,
            'authors': True,
            'paper_id': True,
            'announced_date': True,
            'umap_x': False,
            'umap_y': False
        },
        title=f"Showing {len(filtered_df)} papers"
    )
    
    fig.update_traces(
        marker=dict(size=8, opacity=0.7, line=dict(width=0.5, color='white')),
        hovertemplate='<b>%{customdata[0]}</b><br>' +
                      'Authors: %{customdata[1]}<br>' +
                      'arXiv: %{customdata[2]}<br>' +
                      'Date: %{customdata[3]}<br>' +
                      '<extra></extra>'
    )
    
    fig.update_layout(
        xaxis_title="",
        yaxis_title="",
        hovermode='closest',
        plot_bgcolor='#f8f9fa',
        paper_bgcolor='white',
        legend=dict(
            orientation="v",
            yanchor="top",
            y=1,
            xanchor="left",
            x=1.02,
            font=dict(size=10)
        ),
        margin=dict(l=40, r=120, t=60, b=40)
    )
    
    # Store filtered data for paper details callback
    return fig, filtered_df.to_json(date_format='iso', orient='split')

@app.callback(
    Output('paper-details', 'children'),
    [Input('scatter-plot', 'clickData'),
     Input('filtered-data-store', 'data')],
    prevent_initial_call=False
)
def display_paper_details(clickData, filtered_data_json):
    """Show paper details when a point is clicked."""
    
    if not clickData or not filtered_data_json:
        return html.Div([
            html.H3("Paper Details", style={'color': '#2c3e50', 'borderBottom': '2px solid #3498db'}),
            html.P("Click on a point in the scatter plot to see paper details", style={
                'color': '#7f8c8d',
                'fontStyle': 'italic',
                'marginTop': '20px'
            })
        ])
    
    # Load filtered dataframe
    filtered_df = pd.read_json(filtered_data_json, orient='split')
    
    # Get clicked point index
    point_idx = clickData['points'][0]['pointIndex']
    paper = filtered_df.iloc[point_idx]
    
    return html.Div([
        html.H3("Paper Details", style={'color': '#2c3e50', 'borderBottom': '2px solid #3498db', 'paddingBottom': '10px'}),
        
        html.H4(paper['title'], style={'color': '#34495e', 'marginTop': '15px', 'lineHeight': '1.4'}),
        
        html.P([
            html.Strong("Authors: "),
            html.Span(paper['authors'])
        ], style={'marginTop': '10px'}),
        
        html.P([
            html.Strong("arXiv ID: "),
            html.A(
                paper['paper_id'],
                href=paper['arxiv_url'],
                target="_blank",
                style={'color': '#3498db', 'textDecoration': 'none'}
            ),
            html.Span(" | ", style={'margin': '0 5px'}),
            html.A(
                "PDF",
                href=paper['pdf_url'],
                target="_blank",
                style={'color': '#e74c3c', 'textDecoration': 'none'}
            )
        ]),
        
        html.P([
            html.Strong("Date: "),
            html.Span(paper['announced_date'])
        ]),
        
        html.P([
            html.Strong("Category: "),
            html.Span(paper['primary_category'], style={
                'backgroundColor': '#3498db',
                'color': 'white',
                'padding': '2px 8px',
                'borderRadius': '3px',
                'fontSize': '12px'
            })
        ]),
        
        html.P([
            html.Strong("All Categories: "),
            html.Span(paper['categories'], style={'fontSize': '14px'})
        ]),
        
        html.P([
            html.Strong("Citations: "),
            html.Span(f"S2: {paper['citations_s2'] or 'N/A'}, OpenAlex: {paper['citations_oa'] or 'N/A'}")
        ]),
        
        html.Hr(style={'margin': '15px 0'}),
        
        html.Div([
            html.Strong("Abstract:", style={'display': 'block', 'marginBottom': '10px'}),
            html.P(paper['abstract'] if paper['abstract'] else "No abstract available", style={
                'fontSize': '14px',
                'lineHeight': '1.6',
                'color': '#555',
                'textAlign': 'justify'
            })
        ])
    ])

if __name__ == '__main__':
    print(f"📊 Loaded {len(df)} papers")
    print(f"📁 Database: {DB_PATH}")
    print(f"🌐 Starting dashboard on http://0.0.0.0:8050")
    print(f"📱 For Terminus: ssh -L 8050:localhost:8050 user@server")
    app.run(debug=True, host='0.0.0.0', port=8050)
