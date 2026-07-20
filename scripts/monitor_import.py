"""
Monitor import process when approving legal documents.
Shows: candidate status, source creation, and embedding progress.

Usage: python scripts/monitor_import.py [candidate_id]
"""
import asyncio
import sys
from datetime import datetime, timedelta

from open_notebook.database.repository import repo_query


async def check_candidate_status(candidate_id: str = None):
    """Check candidate import status and related source."""
    print("=" * 60)
    print(f"KIEM TRA TRANG THAI IMPORT - {datetime.now().strftime('%H:%M:%S')}")
    print("=" * 60)
    
    # Query recent candidates
    if candidate_id:
        query = "SELECT * FROM legal_crawl_candidate WHERE id = $id FETCH source"
        results = await repo_query(query, {"id": candidate_id})
    else:
        query = """
            SELECT * FROM legal_crawl_candidate 
            ORDER BY updated DESC 
            LIMIT 5
        """
        results = await repo_query(query, {})
    
    if not results:
        print("Khong tim thay candidate nao!")
        return
    
    for candidate in results:
        print(f"\n Candidate: {candidate.get('id', 'N/A')[:20]}...")
        print(f" Title: {candidate.get('title', 'N/A')[:50]}...")
        print(f" Law Number: {candidate.get('law_number', 'N/A')}")
        print(f" Status: {candidate.get('status', 'N/A').upper()}")
        
        # Check if linked to source
        source = candidate.get('source')
        if source:
            print(f" Source ID: {source.get('id', 'N/A')}")
            print(f" Source Title: {source.get('title', 'N/A')[:40]}...")
            print(f" -> DA VAO DB (Source created)")
            
            # Check embeddings
            source_id = source.get('id')
            embed_query = """
                SELECT count() as chunks 
                FROM source_embedding 
                WHERE source = $source_id 
                GROUP ALL
            """
            embed_results = await repo_query(embed_query, {"source_id": source_id})
            if embed_results and len(embed_results) > 0:
                chunks = embed_results[0].get('chunks', 0)
                print(f" Embeddings: {chunks} chunks da tao")
            else:
                print(f" Embeddings: Dang xu ly (chua co chunks)")
        else:
            print(f" -> CHUA VAO DB (Khong co source lien ket)")
        
        print("-" * 40)


async def watch_import(duration_seconds: int = 60):
    """Watch import process for a duration."""
    print(f"\nGiam sat import trong {duration_seconds} giay...")
    print("Bam Ctrl+C de dung\n")
    
    start_time = datetime.now()
    while (datetime.now() - start_time).seconds < duration_seconds:
        await check_candidate_status()
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Doi 10 giay...")
        await asyncio.sleep(10)


async def main():
    if len(sys.argv) > 1:
        candidate_id = sys.argv[1]
        await check_candidate_status(candidate_id)
    else:
        # Check all recent
        await check_candidate_status()
        
        # Option to watch
        print("\n" + "=" * 60)
        response = input("Co muon giam sat lien tuc? (y/n): ")
        if response.lower() == 'y':
            await watch_import(60)


if __name__ == "__main__":
    asyncio.run(main())
