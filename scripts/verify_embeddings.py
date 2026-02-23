#!/usr/bin/env python3
"""Verify that embedding_idx mappings in SQLite match FAISS vectors."""

import sqlite3
import faiss
import numpy as np
from pathlib import Path
from sentence_transformers import SentenceTransformer

from config import DB_PATH, EMBEDDINGS_DIR, EMBEDDINGS

INDEX_PATH = EMBEDDINGS_DIR / "faiss.index"
MODEL_NAME = EMBEDDINGS["model_name"]

# Load database
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# Load FAISS index
index = faiss.read_index(str(INDEX_PATH))
print(f"FAISS index size: {index.ntotal}")

# Count papers with embeddings
total_with_emb = conn.execute(
    "SELECT COUNT(*) FROM papers WHERE embedding_idx IS NOT NULL"
).fetchone()[0]
print(f"Papers with embedding_idx: {total_with_emb}")

# Check for any out-of-bounds indices
max_idx = conn.execute(
    "SELECT MAX(embedding_idx) FROM papers WHERE embedding_idx IS NOT NULL"
).fetchone()[0]
min_idx = conn.execute(
    "SELECT MIN(embedding_idx) FROM papers WHERE embedding_idx IS NOT NULL"
).fetchone()[0]
print(f"embedding_idx range: {min_idx} to {max_idx}")

if max_idx >= index.ntotal:
    print(f"⚠️  WARNING: max embedding_idx ({max_idx}) >= FAISS size ({index.ntotal})")

# Check for duplicate indices
dups = conn.execute("""
    SELECT embedding_idx, COUNT(*) as cnt 
    FROM papers 
    WHERE embedding_idx IS NOT NULL 
    GROUP BY embedding_idx 
    HAVING cnt > 1
""").fetchall()
if dups:
    print(f"⚠️  WARNING: Found {len(dups)} duplicate embedding_idx values!")
    for d in dups[:5]:
        print(f"    idx {d['embedding_idx']}: {d['cnt']} papers")
else:
    print("✓ No duplicate embedding_idx values")

# Get first 10 papers
papers = conn.execute("""
    SELECT paper_id, title, abstract, embedding_idx 
    FROM papers 
    WHERE embedding_idx IS NOT NULL 
    ORDER BY embedding_idx
    LIMIT 10
""").fetchall()

print(f"\nFirst 10 papers with embeddings:")
for p in papers:
    idx = p['embedding_idx']
    pid = p['paper_id'][:20]
    title = p['title'][:50]
    print(f"  idx={idx}: {pid:<20} - {title}...")

# Verify mapping by re-embedding papers and checking similarity
print("\n--- Verification: Re-embed and check similarity ---")
model = SentenceTransformer(MODEL_NAME)

# Sample 5 papers randomly
samples = conn.execute("""
    SELECT paper_id, title, abstract, embedding_idx 
    FROM papers 
    WHERE embedding_idx IS NOT NULL 
    ORDER BY RANDOM()
    LIMIT 5
""").fetchall()

all_match = True
for paper in samples:
    # Re-compute embedding
    text = f"{paper['title']} [SEP] {paper['abstract']}"
    new_embedding = model.encode([text]).astype('float32')
    faiss.normalize_L2(new_embedding)
    
    # Get stored embedding from FAISS
    stored_embedding = index.reconstruct(paper['embedding_idx']).reshape(1, -1)
    
    # Compute cosine similarity
    similarity = np.dot(new_embedding, stored_embedding.T)[0][0]
    
    match = similarity > 0.999
    if not match:
        all_match = False
    
    status = "✓ YES" if match else "✗ NO (MISMATCH!)"
    print(f"\nPaper: {paper['paper_id']}")
    print(f"  embedding_idx: {paper['embedding_idx']}")
    print(f"  Cosine similarity: {similarity:.6f}")
    print(f"  Match: {status}")

print("\n" + "=" * 60)
if all_match:
    print("✓ All sampled embeddings verified correctly!")
else:
    print("✗ VERIFICATION FAILED - embeddings do not match!")

conn.close()
