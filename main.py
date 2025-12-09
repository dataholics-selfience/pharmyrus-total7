"""
Pharmyrus API v7.2 DIAGNOSTIC
Versão para diagnóstico - mostra erros reais e testa múltiplos métodos
"""

import asyncio
import httpx
import re
import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException

app = FastAPI(title="Pharmyrus DIAGNOSTIC", version="7.2")

SERPAPI_KEYS = [
    "3f22448f4d43ce8259fa2f7f6385222323a67c4ce4e72fcc774b43d23812889d",
    "bc20bca64032a7ac59abf330bbdeca80aa79cd72bb208059056b10fb6e33e4bc",
]

key_index = 0

def get_key():
    global key_index
    key = SERPAPI_KEYS[key_index % len(SERPAPI_KEYS)]
    key_index += 1
    return key

async def fetch_raw(url: str, params: dict = None, timeout: float = 30.0) -> dict:
    """Fetch with full error details"""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url, params=params)
            return {
                "status_code": r.status_code,
                "success": r.status_code == 200,
                "data": r.json() if r.status_code == 200 else None,
                "error": r.text[:500] if r.status_code != 200 else None
            }
    except Exception as e:
        return {"success": False, "error": str(e), "status_code": 0}

# ============================================================================
# TEST 1: Testar google_patents_details diretamente
# ============================================================================
async def test_patent_details(wo: str) -> dict:
    """Testa google_patents_details com diferentes formatos"""
    results = {}
    
    # Formato 1: WO2016096610
    params1 = {
        "engine": "google_patents_details",
        "patent_id": wo,
        "api_key": get_key()
    }
    results["format_wo_number"] = await fetch_raw("https://serpapi.com/search.json", params1)
    await asyncio.sleep(0.5)
    
    # Formato 2: Com A1 no final (WO2016096610A1)
    params2 = {
        "engine": "google_patents_details", 
        "patent_id": f"{wo}A1",
        "api_key": get_key()
    }
    results["format_wo_a1"] = await fetch_raw("https://serpapi.com/search.json", params2)
    await asyncio.sleep(0.5)
    
    # Formato 3: Com A2 no final
    params3 = {
        "engine": "google_patents_details",
        "patent_id": f"{wo}A2",
        "api_key": get_key()
    }
    results["format_wo_a2"] = await fetch_raw("https://serpapi.com/search.json", params3)
    
    return results

# ============================================================================
# TEST 2: Testar via google_patents search + serpapi_link
# ============================================================================
async def test_search_chain(wo: str) -> dict:
    """Testa o método search → serpapi_link"""
    results = {}
    
    # Step 1: Search
    params = {
        "engine": "google_patents",
        "q": wo,
        "api_key": get_key(),
        "num": "5"
    }
    search_result = await fetch_raw("https://serpapi.com/search.json", params)
    results["search"] = search_result
    
    if search_result.get("success") and search_result.get("data"):
        organic = search_result["data"].get("organic_results", [])
        results["organic_count"] = len(organic)
        
        if organic:
            first = organic[0]
            results["first_result"] = {
                "title": first.get("title"),
                "publication_number": first.get("publication_number"),
                "patent_id": first.get("patent_id"),
                "serpapi_link": first.get("serpapi_link"),
                "pdf": first.get("pdf"),
                "snippet": first.get("snippet", "")[:200]
            }
            
            # Step 2: Follow serpapi_link
            serpapi_link = first.get("serpapi_link")
            if serpapi_link:
                await asyncio.sleep(0.5)
                # Add API key
                if "api_key=" not in serpapi_link:
                    serpapi_link = f"{serpapi_link}&api_key={get_key()}"
                
                detail_result = await fetch_raw(serpapi_link)
                results["serpapi_link_result"] = {
                    "success": detail_result.get("success"),
                    "status_code": detail_result.get("status_code"),
                    "error": detail_result.get("error")
                }
                
                if detail_result.get("success") and detail_result.get("data"):
                    data = detail_result["data"]
                    results["patent_data"] = {
                        "title": data.get("title"),
                        "abstract": (data.get("abstract") or "")[:300],
                        "assignee": data.get("assignee"),
                        "inventors": data.get("inventors", [])[:5],
                        "filing_date": data.get("filing_date"),
                        "publication_date": data.get("publication_date"),
                        "has_worldwide_applications": "worldwide_applications" in data,
                        "worldwide_applications_keys": list(data.get("worldwide_applications", {}).keys()),
                        "has_family_members": "family_members" in data,
                        "family_members_count": len(data.get("family_members", [])),
                        "has_also_published_as": "also_published_as" in data,
                        "also_published_as_count": len(data.get("also_published_as", []))
                    }
                    
                    # Extract BR patents
                    br_patents = []
                    worldwide = data.get("worldwide_applications", {})
                    for year, apps in worldwide.items():
                        if isinstance(apps, list):
                            for app in apps:
                                doc_id = app.get("document_id", "")
                                if doc_id.startswith("BR"):
                                    br_patents.append({
                                        "number": doc_id,
                                        "filing_date": app.get("filing_date"),
                                        "status": app.get("status"),
                                        "source": "worldwide_applications"
                                    })
                    
                    for member in data.get("family_members", []):
                        doc_id = member.get("document_id", "") or member.get("publication_number", "")
                        if doc_id.startswith("BR"):
                            br_patents.append({
                                "number": doc_id,
                                "title": member.get("title"),
                                "source": "family_members"
                            })
                    
                    for pub in data.get("also_published_as", []):
                        doc_id = pub if isinstance(pub, str) else pub.get("document_id", "")
                        if doc_id.startswith("BR"):
                            br_patents.append({"number": doc_id, "source": "also_published_as"})
                    
                    results["br_patents_found"] = br_patents
    
    return results

# ============================================================================
# ENDPOINT: Diagnóstico completo de uma WO
# ============================================================================
@app.get("/diagnose/{wo}")
async def diagnose_wo(wo: str):
    """Diagnóstico completo de uma WO específica"""
    start = datetime.now()
    
    # Clean WO format
    wo = wo.upper().strip()
    if not wo.startswith("WO"):
        wo = f"WO{wo}"
    
    results = {
        "wo_number": wo,
        "tests": {}
    }
    
    # Test 1: Direct patent details
    results["tests"]["direct_details"] = await test_patent_details(wo)
    
    await asyncio.sleep(1)
    
    # Test 2: Search chain
    results["tests"]["search_chain"] = await test_search_chain(wo)
    
    results["duration_seconds"] = round((datetime.now() - start).total_seconds(), 1)
    
    return results

# ============================================================================
# ENDPOINT: Buscar uma WO e trazer TODO o conteúdo
# ============================================================================
@app.get("/wo/{wo_number}")
async def get_wo_full_content(wo_number: str):
    """Retorna todo o conteúdo de uma WO específica"""
    
    wo = wo_number.upper().strip()
    if not wo.startswith("WO"):
        wo = f"WO{wo}"
    
    result = {
        "wo_number": wo,
        "wo_content": None,
        "br_patents": [],
        "all_countries": {},
        "status": "pending"
    }
    
    # Search for the WO
    params = {
        "engine": "google_patents",
        "q": wo,
        "api_key": get_key(),
        "num": "10"
    }
    
    search = await fetch_raw("https://serpapi.com/search.json", params)
    
    if not search.get("success"):
        result["status"] = "search_failed"
        result["error"] = search.get("error")
        return result
    
    organic = search.get("data", {}).get("organic_results", [])
    if not organic:
        result["status"] = "no_results"
        return result
    
    # Find exact match
    target = None
    for r in organic:
        pub_num = r.get("publication_number", "")
        patent_id = r.get("patent_id", "")
        if wo in pub_num or wo in patent_id:
            target = r
            break
    
    if not target:
        target = organic[0]
    
    result["search_result"] = {
        "title": target.get("title"),
        "publication_number": target.get("publication_number"),
        "snippet": target.get("snippet"),
        "pdf": target.get("pdf")
    }
    
    # Follow serpapi_link
    serpapi_link = target.get("serpapi_link")
    if not serpapi_link:
        result["status"] = "no_serpapi_link"
        return result
    
    await asyncio.sleep(0.5)
    
    if "api_key=" not in serpapi_link:
        serpapi_link = f"{serpapi_link}&api_key={get_key()}"
    
    detail = await fetch_raw(serpapi_link)
    
    if not detail.get("success"):
        result["status"] = "detail_failed"
        result["error"] = detail.get("error")
        return result
    
    data = detail.get("data", {})
    
    # Full WO content
    result["wo_content"] = {
        "title": data.get("title"),
        "abstract": data.get("abstract"),
        "assignee": data.get("assignee"),
        "applicant": data.get("applicant"),
        "inventors": data.get("inventors"),
        "filing_date": data.get("filing_date"),
        "publication_date": data.get("publication_date"),
        "priority_date": data.get("priority_date"),
        "grant_date": data.get("grant_date"),
        "legal_status": data.get("legal_status"),
        "classifications": data.get("classifications", [])[:10],
        "claims": data.get("claims", [])[:5],  # First 5 claims
        "description_preview": (data.get("description") or "")[:1000],
        "pdf": data.get("pdf"),
        "images": data.get("images", [])[:3]
    }
    
    # Extract ALL countries from worldwide_applications
    worldwide = data.get("worldwide_applications", {})
    for year, apps in worldwide.items():
        if isinstance(apps, list):
            for app in apps:
                doc_id = app.get("document_id", "")
                country = doc_id[:2] if doc_id else "??"
                
                if country not in result["all_countries"]:
                    result["all_countries"][country] = []
                
                result["all_countries"][country].append({
                    "number": doc_id,
                    "filing_date": app.get("filing_date"),
                    "publication_date": app.get("publication_date"),
                    "status": app.get("status"),
                    "title": app.get("title"),
                    "year": year
                })
                
                # Collect BR
                if country == "BR":
                    result["br_patents"].append({
                        "number": doc_id,
                        "filing_date": app.get("filing_date"),
                        "status": app.get("status"),
                        "source": "worldwide_applications"
                    })
    
    # Also from family_members
    for member in data.get("family_members", []):
        doc_id = member.get("document_id", "") or member.get("publication_number", "")
        if doc_id.startswith("BR"):
            if not any(p["number"] == doc_id for p in result["br_patents"]):
                result["br_patents"].append({
                    "number": doc_id,
                    "title": member.get("title"),
                    "source": "family_members"
                })
    
    # From also_published_as
    for pub in data.get("also_published_as", []):
        doc_id = pub if isinstance(pub, str) else pub.get("document_id", "")
        if doc_id.startswith("BR"):
            if not any(p["number"] == doc_id for p in result["br_patents"]):
                result["br_patents"].append({
                    "number": doc_id,
                    "source": "also_published_as"
                })
    
    result["status"] = "success"
    result["countries_found"] = list(result["all_countries"].keys())
    result["total_country_patents"] = sum(len(v) for v in result["all_countries"].values())
    result["br_count"] = len(result["br_patents"])
    
    return result

# ============================================================================
# ENDPOINT: Buscar BR diretamente
# ============================================================================
@app.get("/br/{br_number}")
async def get_br_content(br_number: str):
    """Retorna todo o conteúdo de uma patente BR"""
    
    br = br_number.upper().strip()
    if not br.startswith("BR"):
        br = f"BR{br}"
    
    # Try direct details
    params = {
        "engine": "google_patents_details",
        "patent_id": br,
        "api_key": get_key()
    }
    
    result = await fetch_raw("https://serpapi.com/search.json", params)
    
    if result.get("success") and result.get("data"):
        data = result["data"]
        return {
            "br_number": br,
            "status": "success",
            "method": "direct_details",
            "content": {
                "title": data.get("title"),
                "abstract": data.get("abstract"),
                "assignee": data.get("assignee"),
                "applicant": data.get("applicant"),
                "inventors": data.get("inventors"),
                "filing_date": data.get("filing_date"),
                "publication_date": data.get("publication_date"),
                "grant_date": data.get("grant_date"),
                "legal_status": data.get("legal_status"),
                "claims": data.get("claims", [])[:5],
                "classifications": data.get("classifications", [])[:10],
                "pdf": data.get("pdf")
            }
        }
    
    # Try via search
    await asyncio.sleep(0.5)
    
    search_params = {
        "engine": "google_patents",
        "q": br,
        "api_key": get_key()
    }
    
    search = await fetch_raw("https://serpapi.com/search.json", search_params)
    
    if search.get("success"):
        organic = search.get("data", {}).get("organic_results", [])
        if organic:
            serpapi_link = organic[0].get("serpapi_link")
            if serpapi_link:
                await asyncio.sleep(0.5)
                if "api_key=" not in serpapi_link:
                    serpapi_link = f"{serpapi_link}&api_key={get_key()}"
                
                detail = await fetch_raw(serpapi_link)
                if detail.get("success"):
                    data = detail["data"]
                    return {
                        "br_number": br,
                        "status": "success",
                        "method": "search_chain",
                        "content": {
                            "title": data.get("title"),
                            "abstract": data.get("abstract"),
                            "assignee": data.get("assignee"),
                            "applicant": data.get("applicant"),
                            "inventors": data.get("inventors"),
                            "filing_date": data.get("filing_date"),
                            "publication_date": data.get("publication_date"),
                            "grant_date": data.get("grant_date"),
                            "legal_status": data.get("legal_status"),
                            "claims": data.get("claims", [])[:5],
                            "pdf": data.get("pdf")
                        }
                    }
    
    return {
        "br_number": br,
        "status": "failed",
        "error": "Could not retrieve patent details"
    }

# ============================================================================
# ENDPOINT: INPI Direto
# ============================================================================
@app.get("/inpi/{molecule}")
async def search_inpi_direct(molecule: str):
    """Busca direta no INPI"""
    
    results = []
    terms = [molecule, molecule.lower()]
    
    # Portuguese variation
    if molecule.lower().endswith("ide"):
        terms.append(molecule[:-3] + "ida")
    if molecule.lower().endswith("ine"):
        terms.append(molecule[:-3] + "ina")
    
    for term in terms:
        url = f"https://crawler3-production.up.railway.app/api/data/inpi/patents?medicine={term}"
        data = await fetch_raw(url, timeout=60.0)
        
        if data.get("success") and data.get("data"):
            for p in data["data"].get("data", []):
                title = p.get("title", "")
                if title.startswith("BR"):
                    results.append({
                        "number": title.replace(" ", "-"),
                        "applicant": p.get("applicant"),
                        "deposit_date": p.get("depositDate"),
                        "full_text": p.get("fullText", "")[:500],
                        "search_term": term
                    })
    
    # Dedupe
    seen = set()
    unique = []
    for p in results:
        n = re.sub(r'[\s\-/]', '', p["number"]).upper()
        if n not in seen:
            seen.add(n)
            unique.append(p)
    
    return {
        "molecule": molecule,
        "terms_searched": terms,
        "total_found": len(unique),
        "patents": unique
    }

# ============================================================================
# ROOT & HEALTH
# ============================================================================
@app.get("/")
async def root():
    return {
        "api": "Pharmyrus DIAGNOSTIC",
        "version": "7.2",
        "endpoints": {
            "GET /diagnose/{wo}": "Full diagnostic for a WO number",
            "GET /wo/{wo_number}": "Get full content of a WO",
            "GET /br/{br_number}": "Get full content of a BR patent",
            "GET /inpi/{molecule}": "Search INPI directly"
        },
        "examples": {
            "diagnose": "/diagnose/WO2016096610",
            "wo_content": "/wo/WO2016096610",
            "br_content": "/br/BR112017020400",
            "inpi": "/inpi/darolutamide"
        }
    }

@app.get("/health")
async def health():
    return {"status": "healthy", "version": "7.2-diagnostic"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
