"""
Dash + Plotly dashboard for daily-briefs paper exploration.

Features:
- 2D UMAP scatter plot of all papers
- Search (full-text and semantic)
- Filter by category, date, stream
- Click paper → show full details
- Mobile-responsive layout
"""

import json

import dash
from dash import dcc, html, Input, Output, State, ctx
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import sqlite3
from pathlib import Path
import io
from datetime import datetime

# Import config (app.py is now in scripts/ alongside config.py)
from config import CONFIG, DB_PATH, get_db_connection, PROJECT_ROOT
from utils import safe_json_load

# Dashboard settings
MAX_SEARCH_RESULTS = CONFIG.get("dashboard", {}).get("max_search_results", 500)

def load_papers(include_hidden: bool = False) -> pd.DataFrame:
    """Load all papers with UMAP coordinates.
    
    Returns DataFrame with paper metadata for visualization.
    """
    conn = get_db_connection()
    
    where_hidden = "" if include_hidden else "AND hidden = 0"
    query = f"""
        SELECT 
            id, paper_id, title, abstract, authors,
            primary_category, categories, announced_date,
            arxiv_url, pdf_url,
            citations_s2, citations_oa,
            umap_x, umap_y, hidden
        FROM papers
        WHERE umap_x IS NOT NULL AND umap_y IS NOT NULL
          {where_hidden}
        ORDER BY announced_date DESC
    """
    
    try:
        df = pd.read_sql_query(query, conn)
    finally:
        conn.close()
    
    # Parse authors JSON
    df['authors'] = df['authors'].apply(
        lambda x: ', '.join(safe_json_load(x, default=[], warn_fn=print)[:3]) if x else 'Unknown'
    )
    
    return df

def search_papers(query_text: str, include_hidden: bool = False) -> pd.DataFrame:
    """Full-text search on papers.
    
    Returns DataFrame of matching papers for visualization.
    """
    if not query_text or query_text.strip() == "":
        return load_papers(include_hidden=include_hidden)
    
    conn = get_db_connection()
    
    where_hidden = "" if include_hidden else "AND p.hidden = 0"
    search_query = f"""
        SELECT 
            p.id, p.paper_id, p.title, p.abstract, p.authors,
            p.primary_category, p.categories, p.announced_date,
            p.arxiv_url, p.pdf_url,
            p.citations_s2, p.citations_oa,
            p.umap_x, p.umap_y, p.hidden
        FROM papers p
        JOIN papers_fts fts ON p.id = fts.rowid
        WHERE papers_fts MATCH ?
          AND p.umap_x IS NOT NULL
          AND p.umap_y IS NOT NULL
          {where_hidden}
        ORDER BY bm25(papers_fts)
        LIMIT {MAX_SEARCH_RESULTS}
    """
    
    try:
        df = pd.read_sql_query(search_query, conn, params=(query_text,))
    except sqlite3.OperationalError as e:
        # Malformed FTS query
        return pd.DataFrame()
    finally:
        conn.close()
    
    # Parse authors JSON
    df['authors'] = df['authors'].apply(
        lambda x: ', '.join(safe_json_load(x, default=[], warn_fn=print)[:3]) if x else 'Unknown'
    )
    
    return df

# Initialize app
app = dash.Dash(__name__, suppress_callback_exceptions=True)

# Load initial data
df = load_papers()

# Handle empty dataset for date picker defaults
if df.empty:
    default_start_date = default_end_date = datetime.today().strftime("%Y-%m-%d")
    date_picker_disabled = True
else:
    default_start_date = df['announced_date'].min()
    default_end_date = df['announced_date'].max()
    date_picker_disabled = False

# Get unique categories for filter
all_categories = set()
for cats in df['categories'].dropna():
    all_categories.update(safe_json_load(cats, default=[], warn_fn=print))
category_options = [{'label': cat, 'value': cat} for cat in sorted(all_categories)]

app.layout = html.Div([
    # Header (compact for mobile)
    html.Div([
        html.H1("Daily Briefs", style={
            'textAlign': 'center',
            'color': '#2c3e50',
            'marginBottom': '5px',
            'fontSize': '24px'
        }),
        html.P(f"{len(df)} papers", style={
            'textAlign': 'center',
            'color': '#7f8c8d',
            'fontSize': '14px',
            'margin': '0'
        })
    ], style={'padding': '10px', 'backgroundColor': '#ecf0f1'}),
    
    # Search and filters (stacked for mobile)
    html.Div([
        # Search box
        dcc.Input(
            id='search-box',
            type='text',
            placeholder='Search papers...',
            style={
                'width': '100%',
                'padding': '12px',
                'fontSize': '16px',
                'borderRadius': '5px',
                'border': '1px solid #bdc3c7',
                'boxSizing': 'border-box',
                'marginBottom': '10px'
            },
            debounce=True
        ),
        
        # Category filter (full width)
        html.Div([
            dcc.Dropdown(
                id='category-filter',
                options=[{'label': 'All Categories', 'value': 'all'}] + category_options,
                value='all',
                clearable=False,
                style={'fontSize': '14px'}
            )
        ], style={'marginBottom': '10px'}),
        
        # Show hidden toggle
        html.Div([
            dcc.Checklist(
                id='show-hidden',
                options=[{'label': 'Show hidden papers', 'value': 'show'}],
                value=[],
                style={'fontSize': '12px'}
            )
        ], style={'marginBottom': '10px'}),
        
        # Date range
        html.Div([
            dcc.DatePickerRange(
                id='date-filter',
                start_date=default_start_date,
                end_date=default_end_date,
                style={'fontSize': '12px'},
                disabled=date_picker_disabled
            )
        ], style={'marginBottom': '10px'})
    ], style={
        'padding': '10px',
        'backgroundColor': '#ffffff',
        'margin': '10px',
        'borderRadius': '5px',
        'boxShadow': '0 2px 4px rgba(0,0,0,0.1)'
    }),
    
    # Scatter plot (full width, shorter for mobile)
    html.Div([
        dcc.Graph(
            id='scatter-plot',
            style={'height': '50vh', 'minHeight': '300px'},
            config={
                'displayModeBar': False,
                'displaylogo': False,
                'scrollZoom': True
            }
        )
    ], style={'padding': '0 10px'}),
    
    # Paper details (below plot on mobile)
    html.Div([
        html.Div(id='paper-details', style={
            'padding': '15px',
            'backgroundColor': '#ffffff',
            'borderRadius': '5px',
            'boxShadow': '0 2px 4px rgba(0,0,0,0.1)'
        })
    ], style={'padding': '10px'}),
    
    # Stores
    dcc.Store(id='filtered-data-store'),
    dcc.Store(id='selected-paper-id'),
    dcc.Store(id='refresh-token')
], style={
    'fontFamily': '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
    'backgroundColor': '#f5f6fa',
    'minHeight': '100vh',
    'maxWidth': '100vw',
    'overflowX': 'hidden'
})

@app.callback(
    [Output('scatter-plot', 'figure'),
     Output('filtered-data-store', 'data')],
    [Input('search-box', 'value'),
     Input('category-filter', 'value'),
     Input('show-hidden', 'value'),
     Input('date-filter', 'start_date'),
     Input('date-filter', 'end_date'),
     Input('refresh-token', 'data')]
)
def update_scatter(search_query, category, show_hidden, start_date, end_date, _refresh_token):
    """Update scatter plot based on search and filters."""
    
    include_hidden = show_hidden is not None and 'show' in show_hidden
    
    # Load data (with search if provided)
    if search_query and search_query.strip():
        filtered_df = search_papers(search_query, include_hidden=include_hidden)
    else:
        filtered_df = load_papers(include_hidden=include_hidden)
    
    # Apply category filter (categories is JSON array)
    if category != 'all' and not filtered_df.empty:
        filtered_df = filtered_df[filtered_df['categories'].apply(
            lambda x: category in safe_json_load(x, default=[], warn_fn=print) if x else False
        )]
    
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
        return fig, filtered_df.to_json(date_format='iso', orient='split')
    
    # Create scatter plot
    fig = px.scatter(
        filtered_df,
        x='umap_x',
        y='umap_y',
        color='primary_category',
        custom_data=['id', 'title', 'authors', 'paper_id', 'announced_date'],
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
        hovertemplate='<b>%{customdata[1]}</b><br>' +
                      'Authors: %{customdata[2]}<br>' +
                      'arXiv: %{customdata[3]}<br>' +
                      'Date: %{customdata[4]}<br>' +
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
    [Output('paper-details', 'children'),
     Output('selected-paper-id', 'data')],
    [Input('scatter-plot', 'clickData'),
     Input('filtered-data-store', 'data')],
    prevent_initial_call=False
)
def display_paper_details(clickData, filtered_data_json):
    """Show paper details when a point is clicked."""
    
    if not clickData or not filtered_data_json:
        return html.Div([
            html.H3("Paper Details", style={'color': '#2c3e50', 'borderBottom': '2px solid #3498db'}),
            html.P("Tap a dot above to see paper details", style={
                'color': '#7f8c8d',
                'fontStyle': 'italic',
                'marginTop': '20px'
            })
        ]), None
    
    # Load filtered dataframe (ensure JSON string is parsed correctly)
    filtered_df = pd.read_json(io.StringIO(filtered_data_json), orient='split')
    
    # Use stable ID from customdata to avoid trace index mismatch
    clicked_id = clickData['points'][0]['customdata'][0]
    matching = filtered_df[filtered_df['id'] == clicked_id]
    
    # Handle stale selection (paper no longer in filtered data)
    if matching.empty:
        return html.Div([
            html.H3("Paper Details", style={'color': '#2c3e50', 'borderBottom': '2px solid #3498db'}),
            html.P("Paper not found (may have been filtered out or hidden)", style={
                'color': '#e74c3c',
                'fontStyle': 'italic',
                'marginTop': '20px'
            })
        ]), None
    
    paper = matching.iloc[0]
    
    hidden = int(paper.get('hidden', 0))
    btn_label = "Unhide this paper" if hidden else "Hide this paper"
    btn_color = '#2ecc71' if hidden else '#e74c3c'
    
    return html.Div([
        html.H3("Paper Details", style={'color': '#2c3e50', 'borderBottom': '2px solid #3498db', 'paddingBottom': '10px'}),
        
        html.H4(paper['title'], style={'color': '#34495e', 'marginTop': '15px', 'lineHeight': '1.4'}),
        
        html.Div([
            html.Button(
                btn_label,
                id='toggle-hide-btn',
                n_clicks=0,
                style={
                    'backgroundColor': btn_color,
                    'color': 'white',
                    'border': 'none',
                    'padding': '6px 10px',
                    'borderRadius': '4px',
                    'fontSize': '12px',
                    'marginBottom': '10px'
                }
            )
        ]),
        
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
            html.Span(
                ', '.join(safe_json_load(paper['categories'], default=[], warn_fn=print)) if paper['categories'] else 'None',
                style={'fontSize': '14px'}
            )
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
    ]), int(paper['id'])

@app.callback(
    Output('refresh-token', 'data'),
    Input('toggle-hide-btn', 'n_clicks'),
    State('selected-paper-id', 'data'),
    prevent_initial_call=True
)
def toggle_hide_paper(n_clicks, paper_id):
    if not n_clicks or not paper_id:
        return dash.no_update
    
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT hidden FROM papers WHERE id = ?", (paper_id,))
        row = cur.fetchone()
        if row is None:
            return dash.no_update
        new_hidden = 0 if row[0] else 1
        cur.execute("UPDATE papers SET hidden = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (new_hidden, paper_id))
        conn.commit()
        return datetime.utcnow().isoformat()
    finally:
        conn.close()

if __name__ == '__main__':
    # Get dashboard settings from config
    dashboard_config = CONFIG.get('dashboard', {})
    host = dashboard_config.get('host', '127.0.0.1')
    port = dashboard_config.get('port', 8050)
    
    print(f"📊 Loaded {len(df)} papers")
    print(f"📁 Database: {DB_PATH}")
    print(f"🌐 Starting dashboard on http://{host}:{port}")
    print(f"📱 For Terminus: ssh -L {port}:localhost:{port} user@server")
    app.run(debug=False, host=host, port=port)
