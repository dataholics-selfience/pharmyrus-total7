"""
Pharmyrus Patent Search API v7.3 FIXED
- Usa google_patents SEARCH (não details)
- Segue serpapi_link para worldwide_applications
- Extrai BRs corretamente
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import asyncio
import re
from typing import Optional
import time

app = FastAPI(title="Pharmyrus Patent Search", version="7.3-FIXED")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pool de API keys
API_KEYS = [
    "bc20bca64032a7ac59abf330bbdeca80aa79cd72bb208059056b10fb6e33e4bc",
    "3f22448f4d43ce8259fa2f7f6385222323a67c4ce4e72fcc774b43d23812889d",
    "aee186ce5f9d963fec16c3a6d2ad714781e8ec498f9bb03df330941e3267c568",
]
key_index = 0

def get_api_key() -> str:
    global key_index
    key = API_KEYS[key_index % len(API_KEYS)]
    key_index += 1
    return key

# ============== FUNÇÕES AUXILIARES ==============

async def fetch_json(url: str, params: dict, timeout: float = 20.0) -> dict:
    """Fetch JSON com timeout"""
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, params=params)
        if resp.status_code == 200:
            return resp.json()
        return {}

async def fetch_url(url: str, timeout: float = 20.0) -> dict:
    """Fetch URL direta"""
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url)
        if resp.status_code == 200:
            return resp.json()
        return {}

# ============== MÉTODO CORRETO: SEARCH → SERPAPI_LINK ==============

async def get_wo_details_via_search(wo_number: str) -> dict:
    """
    Método correto:
    1. Busca WO via google_patents search
    2. Segue serpapi_link para detalhes
    3. Extrai worldwide_applications
    """
    result = {
        "wo_number": wo_number,
        "success": False,
        "method": "search_chain",
        "search_step": None,
        "details_step": None,
        "worldwide_applications": {},
        "br_patents": [],
        "error": None
    }
    
    try:
        # Step 1: Search
        search_params = {
            "engine": "google_patents",
            "q": wo_number,
            "api_key": get_api_key(),
            "num": "10"  # Mínimo é 10
        }
        
        search_data = await fetch_json("https://serpapi.com/search.json", search_params)
        
        result["search_step"] = {
            "success": bool(search_data),
            "results_count": len(search_data.get("organic_results", []))
        }
        
        if not search_data or "organic_results" not in search_data:
            result["error"] = "Search returned no results"
            return result
        
        # Encontrar o resultado que corresponde à WO
        serpapi_link = None
        for r in search_data.get("organic_results", []):
            patent_id = r.get("patent_id", "")
            if wo_number.replace("-", "") in patent_id.replace("-", ""):
                serpapi_link = r.get("serpapi_link")
                break
        
        # Se não encontrou exato, pega o primeiro
        if not serpapi_link and search_data.get("organic_results"):
            serpapi_link = search_data["organic_results"][0].get("serpapi_link")
        
        if not serpapi_link:
            result["error"] = "No serpapi_link found"
            return result
        
        # Step 2: Follow serpapi_link
        # Adiciona API key se não tiver
        if "api_key=" not in serpapi_link:
            serpapi_link += f"&api_key={get_api_key()}"
        
        details_data = await fetch_url(serpapi_link)
        
        result["details_step"] = {
            "success": bool(details_data),
            "has_worldwide": "worldwide_applications" in details_data
        }
        
        if not details_data:
            result["error"] = "Details fetch failed"
            return result
        
        # Step 3: Extrair worldwide_applications
        worldwide = details_data.get("worldwide_applications", {})
        result["worldwide_applications"] = worldwide
        
        # Extrair BRs
        br_patents = []
        for year, apps in worldwide.items():
            if isinstance(apps, list):
                for app in apps:
                    doc_id = app.get("document_id", "")
                    if doc_id.startswith("BR"):
                        br_patents.append({
                            "number": doc_id,
                            "filing_date": app.get("filing_date", ""),
                            "status": app.get("status", ""),
                            "source": "worldwide_applications",
                            "year": year
                        })
        
        # Também checar family_members e also_published_as
        for field in ["family_members", "also_published_as"]:
            items = details_data.get(field, [])
            if isinstance(items, list):
                for item in items:
                    doc_id = item.get("document_id", "") if isinstance(item, dict) else str(item)
                    if doc_id.startswith("BR") and not any(p["number"] == doc_id for p in br_patents):
                        br_patents.append({
                            "number": doc_id,
                            "source": field
                        })
        
        result["br_patents"] = br_patents
        result["success"] = True
        
    except Exception as e:
        result["error"] = str(e)
    
    return result

# ============== INPI DIRETO ==============

async def search_inpi(molecule: str) -> list:
    """Busca direta no INPI"""
    results = []
    
    # Variações de nome
    variations = [molecule]
    
    # Português
    pt_map = {
        "darolutamide": "darolutamida",
        "enzalutamide": "enzalutamida",
        "abiraterone": "abiraterona",
    }
    mol_lower = molecule.lower()
    if mol_lower in pt_map:
        variations.append(pt_map[mol_lower])
    
    seen = set()
    
    for term in variations:
        try:
            url = f"https://crawler3-production.up.railway.app/api/data/inpi/patents?medicine={term}"
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    patents = data.get("data", [])
                    for p in patents:
                        title = p.get("title", "")
                        if title.startswith("BR") and title not in seen:
                            seen.add(title)
                            results.append({
                                "number": title.replace(" ", "-"),
                                "applicant": p.get("applicant", ""),
                                "deposit_date": p.get("depositDate", ""),
                                "source": "inpi_direct",
                                "search_term": term
                            })
        except:
            pass
    
    return results

# ============== PUBCHEM ==============

async def get_pubchem_data(molecule: str) -> dict:
    """Busca dados no PubChem"""
    result = {"dev_codes": [], "cas": None, "synonyms": []}
    
    try:
        url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{molecule}/synonyms/JSON"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                syns = data.get("InformationList", {}).get("Information", [{}])[0].get("Synonym", [])
                
                dev_pattern = re.compile(r'^[A-Z]{2,5}[-\s]?\d{3,7}[A-Z]?$', re.IGNORECASE)
                cas_pattern = re.compile(r'^\d{2,7}-\d{2}-\d$')
                
                for s in syns[:100]:
                    if dev_pattern.match(s) and len(result["dev_codes"]) < 10:
                        result["dev_codes"].append(s)
                    if cas_pattern.match(s) and not result["cas"]:
                        result["cas"] = s
                
                result["synonyms"] = syns[:20]
    except:
        pass
    
    return result

# ============== WO DISCOVERY ==============

async def discover_wo_numbers(molecule: str, dev_codes: list) -> list:
    """Descobre WO numbers via múltiplas buscas"""
    wo_numbers = set()
    wo_pattern = re.compile(r'WO[\s-]?(\d{4})[\s/]?(\d{6})', re.IGNORECASE)
    
    # Queries de busca
    queries = [
        f"{molecule} patent WO",
        f'"{molecule}" WIPO patent',
        f"{molecule} Bayer patent WO",
        f"{molecule} Orion patent WO",
    ]
    
    # Adiciona dev codes
    for code in dev_codes[:3]:
        queries.append(f"{code} patent WO")
    
    # Anos específicos
    for year in ["2011", "2016", "2018", "2019", "2020", "2021"]:
        queries.append(f"{molecule} patent WO{year}")
    
    async def search_query(query: str):
        try:
            params = {
                "engine": "google",
                "q": query,
                "api_key": get_api_key(),
                "num": "10"
            }
            data = await fetch_json("https://serpapi.com/search.json", params, timeout=15.0)
            
            found = []
            for r in data.get("organic_results", []):
                text = f"{r.get('title', '')} {r.get('snippet', '')} {r.get('link', '')}"
                for match in wo_pattern.finditer(text):
                    wo = f"WO{match.group(1)}{match.group(2)}"
                    found.append(wo)
            return found
        except:
            return []
    
    # Executa em paralelo (limitado)
    tasks = [search_query(q) for q in queries[:8]]  # Limita queries
    results = await asyncio.gather(*tasks)
    
    for wos in results:
        wo_numbers.update(wos)
    
    return list(wo_numbers)

# ============== ENDPOINTS ==============

@app.get("/")
async def root():
    return {
        "service": "Pharmyrus Patent Search",
        "version": "7.3-FIXED",
        "fix": "Usa google_patents SEARCH + serpapi_link (não details)",
        "endpoints": {
            "/search/{molecule}": "Busca completa de patentes BR",
            "/wo/{wo_number}": "Detalhes de uma WO específica",
            "/inpi/{molecule}": "Busca direta no INPI",
            "/test/{wo_number}": "Testa extração de uma WO"
        }
    }

@app.get("/test/{wo_number}")
async def test_wo(wo_number: str):
    """Testa extração de uma WO específica"""
    start = time.time()
    
    # Normaliza
    wo = wo_number.upper().replace("-", "").replace(" ", "")
    if not wo.startswith("WO"):
        wo = f"WO{wo}"
    
    result = await get_wo_details_via_search(wo)
    result["duration_seconds"] = round(time.time() - start, 2)
    
    return result

@app.get("/wo/{wo_number}")
async def get_wo(wo_number: str):
    """Obtém detalhes completos de uma WO"""
    start = time.time()
    
    # Normaliza
    wo = wo_number.upper().replace("-", "").replace(" ", "")
    if not wo.startswith("WO"):
        wo = f"WO{wo}"
    
    result = await get_wo_details_via_search(wo)
    
    return {
        "wo_number": wo,
        "success": result["success"],
        "br_patents": result["br_patents"],
        "br_count": len(result["br_patents"]),
        "all_countries": list(result["worldwide_applications"].keys()),
        "total_countries": len(result["worldwide_applications"]),
        "method": result["method"],
        "steps": {
            "search": result["search_step"],
            "details": result["details_step"]
        },
        "error": result["error"],
        "duration_seconds": round(time.time() - start, 2)
    }

@app.get("/inpi/{molecule}")
async def inpi_search(molecule: str):
    """Busca direta no INPI"""
    start = time.time()
    
    results = await search_inpi(molecule)
    
    return {
        "molecule": molecule,
        "br_patents": results,
        "count": len(results),
        "source": "inpi_direct",
        "duration_seconds": round(time.time() - start, 2)
    }

@app.get("/search/{molecule}")
async def search_patents(molecule: str, brand: Optional[str] = None):
    """
    Busca completa de patentes BR para uma molécula
    
    Fluxo:
    1. PubChem → dev codes, CAS
    2. Google → WO numbers
    3. Para cada WO: search → serpapi_link → worldwide_applications → BRs
    4. INPI direto → BRs adicionais
    """
    start = time.time()
    
    # Step 1: PubChem
    pubchem = await get_pubchem_data(molecule)
    
    # Step 2: Discover WOs
    wo_numbers = await discover_wo_numbers(molecule, pubchem["dev_codes"])
    
    # Ordena por ano (mais recentes primeiro) e limita
    def get_year(wo):
        match = re.search(r'WO(\d{4})', wo)
        return int(match.group(1)) if match else 0
    
    sorted_wos = sorted(wo_numbers, key=get_year, reverse=True)
    top_wos = sorted_wos[:20]  # Limita a 20
    
    # Step 3: Process WOs em paralelo
    wo_tasks = [get_wo_details_via_search(wo) for wo in top_wos]
    wo_results = await asyncio.gather(*wo_tasks)
    
    # Coleta BRs de WOs
    br_from_wo = []
    wo_details = []
    
    for wr in wo_results:
        wo_details.append({
            "wo_number": wr["wo_number"],
            "success": wr["success"],
            "br_count": len(wr["br_patents"]),
            "error": wr["error"]
        })
        
        for br in wr["br_patents"]:
            if not any(b["number"] == br["number"] for b in br_from_wo):
                br_from_wo.append(br)
    
    # Step 4: INPI direto
    inpi_results = await search_inpi(molecule)
    
    # Merge (evita duplicatas)
    all_br = br_from_wo.copy()
    for br in inpi_results:
        if not any(b["number"] == br["number"] for b in all_br):
            all_br.append(br)
    
    duration = round(time.time() - start, 2)
    
    return {
        "molecule": molecule,
        "brand": brand,
        "metadata": {
            "dev_codes": pubchem["dev_codes"],
            "cas": pubchem["cas"]
        },
        "wo_discovery": {
            "total_found": len(wo_numbers),
            "processed": len(top_wos),
            "wo_numbers": sorted_wos
        },
        "wo_processing": {
            "results": wo_details,
            "successful": sum(1 for w in wo_details if w["success"]),
            "with_br": sum(1 for w in wo_details if w["br_count"] > 0)
        },
        "br_patents": {
            "from_wo": len(br_from_wo),
            "from_inpi": len(inpi_results),
            "total": len(all_br),
            "patents": all_br
        },
        "comparison": {
            "expected_cortellis": 8,
            "found": len(all_br),
            "match_rate": f"{min(100, round(len(all_br) / 8 * 100))}%"
        },
        "performance": {
            "duration_seconds": duration,
            "wo_processed": len(top_wos),
            "api_calls_estimate": len(top_wos) * 2 + 8  # search + details per WO + discovery
        }
    }

# Health check
@app.get("/health")
async def health():
    return {"status": "healthy", "version": "7.3-FIXED"}
