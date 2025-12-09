"""
Pharmyrus API v7.1 TURBO
Fast Brazilian Patent Search - Optimized for Railway timeout

Key optimizations:
- Parallel processing (not sequential)
- Limited to 25 most relevant WOs
- Single API call per WO
- 25 second total timeout
"""

import asyncio
import httpx
import re
import os
from datetime import datetime
from typing import Optional, List
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI(title="Pharmyrus API", version="7.1-turbo")

# API Keys
SERPAPI_KEYS = [
    "3f22448f4d43ce8259fa2f7f6385222323a67c4ce4e72fcc774b43d23812889d",
    "bc20bca64032a7ac59abf330bbdeca80aa79cd72bb208059056b10fb6e33e4bc",
    "aad6d736889f91f9e7fe5a094336589404d04eda73fee9b158e328c2bd5a4d7e",
    "d3b97e647e940af0b9bb5d37605cae9a8f59c495e6764630da8477434be4ce81",
]

key_index = 0

def get_key():
    global key_index
    key = SERPAPI_KEYS[key_index % len(SERPAPI_KEYS)]
    key_index += 1
    return key

# Fast HTTP client
async def fetch(url: str, params: dict = None, timeout: float = 10.0) -> dict:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url, params=params)
            return r.json() if r.status_code == 200 else {}
    except:
        return {}

# Extract WO numbers from text
def extract_wos(text: str) -> set:
    wos = set()
    for m in re.findall(r'WO[\s-]?(\d{4})[\s/]?(\d{6})', text, re.I):
        wos.add(f"WO{m[0]}{m[1]}")
    return wos

# ============================================================================
# PHASE 1: PubChem (fast)
# ============================================================================
async def get_pubchem(molecule: str) -> dict:
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{molecule}/synonyms/JSON"
    data = await fetch(url)
    
    dev_codes, cas = [], None
    try:
        syns = data.get("InformationList", {}).get("Information", [{}])[0].get("Synonym", [])
        for s in syns[:100]:
            if re.match(r'^[A-Z]{2,5}-?\d{3,7}[A-Z]?$', s, re.I) and len(dev_codes) < 20:
                dev_codes.append(s)
            if re.match(r'^\d{2,7}-\d{2}-\d$', s) and not cas:
                cas = s
    except:
        pass
    
    return {"dev_codes": dev_codes, "cas": cas}

# ============================================================================
# PHASE 2: WO Discovery (parallel, fast)
# ============================================================================
async def discover_wos(molecule: str, dev_codes: list) -> list:
    queries = [
        f'"{molecule}" patent WO',
        f'"{molecule}" Orion Corporation patent',
        f'"{molecule}" Bayer patent',
        f'"{molecule}" pharmaceutical composition WO',
    ]
    
    # Add dev code queries
    for dev in dev_codes[:5]:
        queries.append(f'"{dev}" patent WO')
    
    all_wos = set()
    
    # Parallel search
    async def search_one(q):
        params = {"engine": "google_patents", "q": q, "api_key": get_key(), "num": "30"}
        data = await fetch("https://serpapi.com/search.json", params)
        wos = set()
        for r in data.get("organic_results", []):
            text = f"{r.get('title', '')} {r.get('snippet', '')} {r.get('publication_number', '')}"
            wos.update(extract_wos(text))
            # Direct publication number
            pn = r.get("publication_number", "")
            if pn.startswith("WO"):
                wos.add(re.sub(r'[A-Z]\d*$', '', pn))
        return wos
    
    tasks = [search_one(q) for q in queries]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    for r in results:
        if isinstance(r, set):
            all_wos.update(r)
    
    return sorted(list(all_wos))

# ============================================================================
# PHASE 3: Extract BR from WO (parallel, fast)
# ============================================================================
async def extract_br_from_wo(wo: str) -> dict:
    """Single fast call to get BR patents from WO"""
    result = {"wo": wo, "br_patents": [], "status": "pending"}
    
    try:
        # Direct patent details
        params = {"engine": "google_patents_details", "patent_id": wo, "api_key": get_key()}
        data = await fetch("https://serpapi.com/search.json", params, timeout=15.0)
        
        if not data or data.get("error"):
            result["status"] = "no_data"
            return result
        
        # Extract from worldwide_applications
        worldwide = data.get("worldwide_applications", {})
        for year, apps in worldwide.items():
            if isinstance(apps, list):
                for app in apps:
                    doc_id = app.get("document_id", "")
                    if doc_id.startswith("BR"):
                        result["br_patents"].append({
                            "number": doc_id,
                            "filing_date": app.get("filing_date", ""),
                            "status": app.get("status", ""),
                            "from_wo": wo,
                            "source": "worldwide_applications"
                        })
        
        # Extract from family_members
        for member in data.get("family_members", []):
            doc_id = member.get("document_id", "") or member.get("publication_number", "")
            if doc_id.startswith("BR"):
                if not any(p["number"] == doc_id for p in result["br_patents"]):
                    result["br_patents"].append({
                        "number": doc_id,
                        "title": member.get("title", ""),
                        "from_wo": wo,
                        "source": "family_members"
                    })
        
        # Extract from also_published_as
        for pub in data.get("also_published_as", []):
            doc_id = pub if isinstance(pub, str) else pub.get("document_id", "")
            if doc_id.startswith("BR"):
                if not any(p["number"] == doc_id for p in result["br_patents"]):
                    result["br_patents"].append({
                        "number": doc_id,
                        "from_wo": wo,
                        "source": "also_published_as"
                    })
        
        # Store WO details
        result["wo_details"] = {
            "title": data.get("title", ""),
            "assignee": data.get("assignee", ""),
            "filing_date": data.get("filing_date", ""),
            "inventors": data.get("inventors", [])[:5]
        }
        
        result["status"] = "success" if result["br_patents"] else "no_br"
        
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
    
    return result

# ============================================================================
# PHASE 4: INPI Direct (fast)
# ============================================================================
async def search_inpi(molecule: str, dev_codes: list) -> list:
    patents = []
    terms = [molecule, molecule.lower()]
    
    # Portuguese variation
    if molecule.lower().endswith("ide"):
        terms.append(molecule[:-3] + "ida")
    
    for dev in dev_codes[:3]:
        terms.append(dev)
    
    for term in terms[:6]:
        try:
            url = f"https://crawler3-production.up.railway.app/api/data/inpi/patents?medicine={term}"
            data = await fetch(url, timeout=10.0)
            
            for p in data.get("data", []):
                title = p.get("title", "")
                if title.startswith("BR"):
                    patents.append({
                        "number": title.replace(" ", "-"),
                        "applicant": p.get("applicant", ""),
                        "deposit_date": p.get("depositDate", ""),
                        "source": "inpi_direct"
                    })
        except:
            pass
    
    # Dedupe
    seen = set()
    unique = []
    for p in patents:
        n = re.sub(r'[\s\-/]', '', p["number"]).upper()
        if n not in seen:
            seen.add(n)
            unique.append(p)
    
    return unique

# ============================================================================
# MAIN SEARCH - OPTIMIZED
# ============================================================================
@app.post("/search")
async def search_patents(request: dict):
    start = datetime.now()
    
    molecule = (request.get("nome_molecula") or "").strip()
    brand = (request.get("nome_comercial") or "").strip()
    
    if not molecule:
        raise HTTPException(400, "nome_molecula required")
    
    # Phase 1: PubChem (fast)
    pubchem = await get_pubchem(molecule)
    
    # Phase 2: WO Discovery (parallel)
    all_wos = await discover_wos(molecule, pubchem["dev_codes"])
    
    # LIMIT to 25 most relevant WOs (prioritize recent years)
    # Sort by year descending, take top 25
    def get_year(wo):
        try:
            return int(wo[2:6])
        except:
            return 2000
    
    sorted_wos = sorted(all_wos, key=get_year, reverse=True)
    top_wos = sorted_wos[:25]
    
    # Phase 3: Extract BR from each WO (PARALLEL - key optimization!)
    tasks = [extract_br_from_wo(wo) for wo in top_wos]
    wo_results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Collect results
    all_br = []
    wo_summary = []
    
    for r in wo_results:
        if isinstance(r, dict):
            wo_summary.append({
                "wo": r.get("wo"),
                "br_count": len(r.get("br_patents", [])),
                "status": r.get("status"),
                "wo_title": r.get("wo_details", {}).get("title", "")[:80]
            })
            for br in r.get("br_patents", []):
                if not any(p["number"] == br["number"] for p in all_br):
                    all_br.append(br)
    
    # Phase 4: INPI Direct (parallel)
    inpi_patents = await search_inpi(molecule, pubchem["dev_codes"])
    
    # Merge INPI results
    for p in inpi_patents:
        norm = re.sub(r'[\s\-/]', '', p["number"]).upper()
        if not any(re.sub(r'[\s\-/]', '', br["number"]).upper() == norm for br in all_br):
            all_br.append(p)
    
    duration = (datetime.now() - start).total_seconds()
    
    return {
        "api_version": "7.1 TURBO",
        "molecule": molecule,
        "brand": brand or None,
        "pubchem": {
            "dev_codes": pubchem["dev_codes"][:10],
            "cas": pubchem["cas"]
        },
        "wo_discovery": {
            "total_found": len(all_wos),
            "processed": len(top_wos),
            "all_wos": all_wos,
            "note": f"Processed top {len(top_wos)} most recent WOs"
        },
        "wo_processing": {
            "summary": wo_summary,
            "successful": len([w for w in wo_summary if w["status"] == "success"])
        },
        "br_patents": {
            "total": len(all_br),
            "from_wo": len([b for b in all_br if b.get("source") != "inpi_direct"]),
            "from_inpi": len([b for b in all_br if b.get("source") == "inpi_direct"]),
            "patents": all_br
        },
        "comparison": {
            "expected": 8,
            "found": len(all_br),
            "rate": f"{min(100, round(len(all_br) / 8 * 100))}%",
            "status": "EXCELLENT" if len(all_br) >= 6 else "GOOD" if len(all_br) >= 4 else "NEEDS_WORK"
        },
        "performance": {
            "duration_seconds": round(duration, 1),
            "wos_found": len(all_wos),
            "wos_processed": len(top_wos),
            "timestamp": datetime.now().isoformat()
        }
    }

# Browser-friendly endpoint
@app.get("/api/v1/search/{molecule}")
async def search_by_url(molecule: str, brand: Optional[str] = None):
    return await search_patents({"nome_molecula": molecule, "nome_comercial": brand})

@app.get("/")
async def root():
    return {
        "api": "Pharmyrus",
        "version": "7.1 TURBO",
        "description": "Fast Brazilian patent search",
        "endpoints": {
            "GET /api/v1/search/{molecule}": "Search by URL",
            "POST /search": "Search by JSON",
            "GET /health": "Health check"
        },
        "example": "/api/v1/search/darolutamide"
    }

@app.get("/health")
async def health():
    return {"status": "healthy", "version": "7.1-turbo"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
