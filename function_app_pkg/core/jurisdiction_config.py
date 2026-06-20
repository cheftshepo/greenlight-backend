# function_app_pkg/core/jurisdiction_config.py
"""
COMPREHENSIVE JURISDICTION CONFIGURATION - MATCHED TO YOUR 8 JURISDICTIONS
==========================================================================
Multiple authoritative sources per jurisdiction for maximum compliance coverage.

YOUR JURISDICTIONS:
🇬🇧 UK, 🇪🇺 EU, 🇺🇸 US, 🇩🇪 DE, 🇫🇷 FR, 🇦🇺 AU, 🇿🇦 ZA, 🌍 GLOBAL

UPDATE FREQUENCY BY CATEGORY:
- ESG/Greenwashing: Monthly (rapidly evolving)
- Crypto/Digital Assets: Monthly (new regulations constantly)
- General Marketing: Quarterly
"""

COMPREHENSIVE_JURISDICTIONS = [
    # =========================================================================
    # 🇬🇧 UNITED KINGDOM - FCA (Financial Conduct Authority)
    # =========================================================================
    {
        "name": "UK-FCA-COBS",
        "jurisdiction": "UK",
        "regulator": "FCA",
        "priority": "critical",
        "update_frequency": "quarterly",
        "sources": [
            {
                "url": "https://www.handbook.fca.org.uk/handbook/COBS/4/",
                "type": "handbook",
                "focus": "COBS 4 - Financial promotions (PRIMARY SOURCE)"
            },
            {
                "url": "https://www.handbook.fca.org.uk/handbook/COBS/2/2.html",
                "type": "handbook",
                "focus": "COBS 2.2 - Information disclosure"
            },
            {
                "url": "https://www.handbook.fca.org.uk/handbook/COBS/13/",
                "type": "handbook",
                "focus": "COBS 13 - Periodic reporting"
            },
            {
                "url": "https://www.handbook.fca.org.uk/handbook/COBS/14/",
                "type": "handbook",
                "focus": "COBS 14 - Providing product information"
            }
        ]
    },
    {
        "name": "UK-FCA-PRIN",
        "jurisdiction": "UK",
        "regulator": "FCA",
        "priority": "critical",
        "update_frequency": "quarterly",
        "sources": [
            {
                "url": "https://www.handbook.fca.org.uk/handbook/PRIN/2/",
                "type": "handbook",
                "focus": "PRIN 2 - Principles for Businesses (FOUNDATIONAL)"
            }
        ]
    },
    {
        "name": "UK-FCA-Consumer-Duty",
        "jurisdiction": "UK",
        "regulator": "FCA",
        "priority": "critical",
        "update_frequency": "monthly",
        "sources": [
            {
                "url": "https://www.fca.org.uk/publication/policy/ps23-15.pdf",
                "type": "pdf",
                "focus": "PS23/15 - Consumer Duty (2023 CRITICAL UPDATE)"
            },
            {
                "url": "https://www.fca.org.uk/publications/policy-statements/ps22-9-strengthening-financial-promotion-rules-high-risk-investments",
                "type": "page",
                "focus": "High-risk investment promotions"
            }
        ]
    },
    {
        "name": "UK-FCA-ESG-Greenwashing",
        "jurisdiction": "UK",
        "regulator": "FCA",
        "priority": "critical",
        "update_frequency": "monthly",
        "sources": [
            {
                "url": "https://www.fca.org.uk/publication/correspondence/dear-ceo-letter-anti-greenwashing-rule.pdf",
                "type": "pdf",
                "focus": "Anti-Greenwashing Rule (SDR) - LATEST 2024"
            },
            {
                "url": "https://www.fca.org.uk/publications/policy-statements/ps23-16-sustainability-disclosure-requirements-sdr-investment-labels",
                "type": "page",
                "focus": "Sustainability Disclosure Requirements (SDR)"
            }
        ]
    },
    
    # =========================================================================
    # 🇪🇺 EUROPEAN UNION - ESMA (European Securities and Markets Authority)
    # =========================================================================
    {
        "name": "EU-MiFID-II",
        "jurisdiction": "EU",
        "regulator": "ESMA",
        "priority": "critical",
        "update_frequency": "quarterly",
        "sources": [
            {
                "url": "https://www.esma.europa.eu/sites/default/files/library/2015/11/2014-1569_-_guidelines_on_mifid_ii_product_governance_requirements.pdf",
                "type": "pdf",
                "focus": "MiFID II Product Governance (PRIMARY)"
            },
            {
                "url": "https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX:32017R0565",
                "type": "pdf",
                "focus": "MiFID II Delegated Regulation (EU) 2017/565"
            }
        ]
    },
    {
        "name": "EU-PRIIPs-KID",
        "jurisdiction": "EU",
        "regulator": "EU Commission",
        "priority": "critical",
        "update_frequency": "bi-annual",
        "sources": [
            {
                "url": "https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX:32014R1286",
                "type": "pdf",
                "focus": "PRIIPs Regulation - Key Information Documents"
            }
        ]
    },
    {
        "name": "EU-SFDR-ESG",
        "jurisdiction": "EU",
        "regulator": "EU Commission",
        "priority": "critical",
        "update_frequency": "monthly",
        "sources": [
            {
                "url": "https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX:32019R2088",
                "type": "pdf",
                "focus": "SFDR - Sustainable Finance Disclosure (CRITICAL)"
            },
            {
                "url": "https://www.esma.europa.eu/sites/default/files/2023-05/ESMA34-472-373_Final_Report_on_Guidelines_on_funds_names.pdf",
                "type": "pdf",
                "focus": "ESG fund naming guidelines (Anti-greenwashing)"
            }
        ]
    },
    {
        "name": "EU-MiCA-Crypto",
        "jurisdiction": "EU",
        "regulator": "EU Commission",
        "priority": "critical",
        "update_frequency": "monthly",
        "sources": [
            {
                "url": "https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX:32023R1114",
                "type": "pdf",
                "focus": "MiCA - Markets in Crypto-Assets (LATEST)"
            }
        ]
    },
    {
        "name": "EU-UCITS",
        "jurisdiction": "EU",
        "regulator": "ESMA",
        "priority": "high",
        "update_frequency": "quarterly",
        "sources": [
            {
                "url": "https://www.esma.europa.eu/document/guidelines-ucits-etfs-and-other-ucits-issues",
                "type": "page",
                "focus": "UCITS marketing guidelines"
            }
        ]
    },
    
    # =========================================================================
    # 🇺🇸 UNITED STATES - SEC (Securities and Exchange Commission)
    # =========================================================================
    {
        "name": "US-SEC-Marketing-Rule",
        "jurisdiction": "US",
        "regulator": "SEC",
        "priority": "critical",
        "update_frequency": "quarterly",
        "sources": [
            {
                "url": "https://www.sec.gov/files/rules/final/2020/ia-5653.pdf",
                "type": "pdf",
                "focus": "Rule 206(4)-1 Marketing Rule (2020 OVERHAUL)"
            },
            {
                "url": "https://www.sec.gov/investment/investment-adviser-marketing",
                "type": "page",
                "focus": "Marketing Rule compliance guidance"
            }
        ]
    },
    {
        "name": "US-SEC-Investment-Advisers",
        "jurisdiction": "US",
        "regulator": "SEC",
        "priority": "critical",
        "update_frequency": "quarterly",
        "sources": [
            {
                "url": "https://www.sec.gov/about/laws/iaa40.pdf",
                "type": "pdf",
                "focus": "Investment Advisers Act 1940 (FOUNDATIONAL)"
            }
        ]
    },
    {
        "name": "US-FINRA-Communications",
        "jurisdiction": "US",
        "regulator": "FINRA",
        "priority": "critical",
        "update_frequency": "quarterly",
        "sources": [
            {
                "url": "https://www.finra.org/rules-guidance/rulebooks/finra-rules/2210",
                "type": "page",
                "focus": "FINRA Rule 2210 - Communications with Public"
            },
            {
                "url": "https://www.finra.org/rules-guidance/rulebooks/finra-rules/2220",
                "type": "page",
                "focus": "FINRA Rule 2220 - Options Communications"
            },
            {
                "url": "https://www.finra.org/rules-guidance/rulebooks/finra-rules/2241",
                "type": "page",
                "focus": "FINRA Rule 2241 - Research Reports"
            }
        ]
    },
    {
        "name": "US-SEC-ESG-Climate",
        "jurisdiction": "US",
        "regulator": "SEC",
        "priority": "critical",
        "update_frequency": "monthly",
        "sources": [
            {
                "url": "https://www.sec.gov/files/rules/interp/2021/im-12532.pdf",
                "type": "pdf",
                "focus": "ESG fund names rule (Anti-greenwashing)"
            }
        ]
    },
    
    # =========================================================================
    # 🇩🇪 GERMANY - BaFin (Federal Financial Supervisory Authority)
    # =========================================================================
    {
        "name": "DE-BaFin-WpHG",
        "jurisdiction": "DE",
        "regulator": "BaFin",
        "priority": "high",
        "update_frequency": "quarterly",
        "sources": [
            {
                "url": "https://www.bafin.de/SharedDocs/Veroeffentlichungen/EN/Fachartikel/2023/fa_bj_2307_MiFID_II_Produktueberwachung_en.html",
                "type": "page",
                "focus": "MiFID II Product Monitoring (German implementation)"
            }
        ]
    },
    {
        "name": "DE-BaFin-Advertising",
        "jurisdiction": "DE",
        "regulator": "BaFin",
        "priority": "high",
        "update_frequency": "quarterly",
        "sources": [
            {
                "url": "https://www.bafin.de/SharedDocs/Veroeffentlichungen/EN/Meldung/2021/meldung_2021_03_01_werbung_fuer_hochriskante_investments_en.html",
                "type": "page",
                "focus": "High-risk investment advertising restrictions"
            }
        ]
    },
    {
        "name": "DE-BaFin-Sustainability",
        "jurisdiction": "DE",
        "regulator": "BaFin",
        "priority": "high",
        "update_frequency": "monthly",
        "sources": [
            {
                "url": "https://www.bafin.de/EN/Aufsicht/Themen/Nachhaltige-Finanzen/nachhaltige-finanzen_node_en.html",
                "type": "page",
                "focus": "Sustainable finance guidance"
            }
        ]
    },
    
    # =========================================================================
    # 🇫🇷 FRANCE - AMF (Autorité des Marchés Financiers)
    # =========================================================================
    {
        "name": "FR-AMF-Marketing",
        "jurisdiction": "FR",
        "regulator": "AMF",
        "priority": "high",
        "update_frequency": "quarterly",
        "sources": [
            {
                "url": "https://www.amf-france.org/en/regulation/policy/doc-2020-05",
                "type": "page",
                "focus": "Marketing communications guide (PRIMARY)"
            }
        ]
    },
    {
        "name": "FR-AMF-ESG",
        "jurisdiction": "FR",
        "regulator": "AMF",
        "priority": "high",
        "update_frequency": "monthly",
        "sources": [
            {
                "url": "https://www.amf-france.org/en/regulation/policy/doc-2022-01",
                "type": "page",
                "focus": "Sustainable finance information (ESG)"
            },
            {
                "url": "https://www.amf-france.org/en/news-publications/news-releases/amf-news-releases/amf-strengthens-its-supervision-esg-related-funds",
                "type": "page",
                "focus": "ESG fund supervision (Anti-greenwashing)"
            }
        ]
    },
    {
        "name": "FR-AMF-Crypto",
        "jurisdiction": "FR",
        "regulator": "AMF",
        "priority": "high",
        "update_frequency": "monthly",
        "sources": [
            {
                "url": "https://www.amf-france.org/en/news-publications/news/advertising-crypto-assets-amf-publishes-its-doctrine",
                "type": "page",
                "focus": "Crypto-asset advertising doctrine"
            }
        ]
    },
    
    # =========================================================================
    # 🇦🇺 AUSTRALIA - ASIC (Australian Securities and Investments Commission)
    # =========================================================================
    {
        "name": "AU-ASIC-RG234",
        "jurisdiction": "AU",
        "regulator": "ASIC",
        "priority": "high",
        "update_frequency": "quarterly",
        "sources": [
            {
                "url": "https://download.asic.gov.au/media/5994021/rg234-published-15-december-2020.pdf",
                "type": "pdf",
                "focus": "RG 234 - Advertising financial products (PRIMARY)"
            }
        ]
    },
    {
        "name": "AU-ASIC-Greenwashing",
        "jurisdiction": "AU",
        "regulator": "ASIC",
        "priority": "critical",
        "update_frequency": "monthly",
        "sources": [
            {
                "url": "https://asic.gov.au/about-asic/news-centre/find-a-media-release/2022-releases/22-274mr-asic-acts-against-greenwashing/",
                "type": "page",
                "focus": "Greenwashing enforcement (LATEST 2024)"
            },
            {
                "url": "https://download.asic.gov.au/media/fnwpn5kv/rep-758-published-20-june-2023.pdf",
                "type": "pdf",
                "focus": "REP 758 - Greenwashing review"
            }
        ]
    },
    {
        "name": "AU-ASIC-Design-Distribution",
        "jurisdiction": "AU",
        "regulator": "ASIC",
        "priority": "high",
        "update_frequency": "quarterly",
        "sources": [
            {
                "url": "https://download.asic.gov.au/media/5230063/rg274-published-5-october-2020.pdf",
                "type": "pdf",
                "focus": "RG 274 - Product design and distribution obligations"
            }
        ]
    },
    
    # =========================================================================
    # 🇿🇦 SOUTH AFRICA - FSCA (Financial Sector Conduct Authority)
    # =========================================================================
    {
        "name": "ZA-FSCA-FAIS",
        "jurisdiction": "ZA",
        "regulator": "FSCA",
        "priority": "high",
        "update_frequency": "quarterly",
        "sources": [
            {
                "url": "https://www.fsca.co.za/Regulatory%20Frameworks/Docs/RFI%202%20of%202003.pdf",
                "type": "pdf",
                "focus": "General Code of Conduct for FSPs (PRIMARY)"
            }
        ]
    },
    {
        "name": "ZA-FSCA-Advertising",
        "jurisdiction": "ZA",
        "regulator": "FSCA",
        "priority": "high",
        "update_frequency": "quarterly",
        "sources": [
            {
                "url": "https://www.fsca.co.za/Regulatory%20Frameworks/Documents/Board%20Notices/2021/BN100%20of%202021%20Code%20of%20Conduct%20for%20Advertising.pdf",
                "type": "pdf",
                "focus": "Code of Conduct for Advertising (BN100)"
            }
        ]
    },
    {
        "name": "ZA-FSCA-Crypto",
        "jurisdiction": "ZA",
        "regulator": "FSCA",
        "priority": "high",
        "update_frequency": "monthly",
        "sources": [
            {
                "url": "https://www.fsca.co.za/Regulatory%20Frameworks/Documents/Position%20Papers/PP2%20of%202022%20-%20Position%20Paper%20on%20Crypto%20Assets.pdf",
                "type": "pdf",
                "focus": "Position Paper on Crypto Assets"
            }
        ]
    },
    {
        "name": "ZA-FSCA-ESG",
        "jurisdiction": "ZA",
        "regulator": "FSCA",
        "priority": "high",
        "update_frequency": "monthly",
        "sources": [
            {
                "url": "https://www.fsca.co.za/Regulatory%20Frameworks/Pages/Conduct-Standard-for-Regulated-Entities.aspx",
                "type": "page",
                "focus": "Conduct standards including ESG disclosures"
            }
        ]
    },
    
    # =========================================================================
    # 🌍 GLOBAL - IOSCO (International Organization of Securities Commissions)
    # =========================================================================
    {
        "name": "GLOBAL-IOSCO-Retail",
        "jurisdiction": "GLOBAL",
        "regulator": "IOSCO",
        "priority": "medium",
        "update_frequency": "bi-annual",
        "sources": [
            {
                "url": "https://www.iosco.org/library/pubdocs/pdf/IOSCOPD561.pdf",
                "type": "pdf",
                "focus": "Retail Investment Product Offering (FOUNDATIONAL)"
            }
        ]
    },
    {
        "name": "GLOBAL-IOSCO-Crypto",
        "jurisdiction": "GLOBAL",
        "regulator": "IOSCO",
        "priority": "high",
        "update_frequency": "monthly",
        "sources": [
            {
                "url": "https://www.iosco.org/library/pubdocs/pdf/IOSCOPD718.pdf",
                "type": "pdf",
                "focus": "Policy Recommendations for Crypto-Assets"
            }
        ]
    },
    {
        "name": "GLOBAL-IOSCO-ESG",
        "jurisdiction": "GLOBAL",
        "regulator": "IOSCO",
        "priority": "high",
        "update_frequency": "monthly",
        "sources": [
            {
                "url": "https://www.iosco.org/library/pubdocs/pdf/IOSCOPD729.pdf",
                "type": "pdf",
                "focus": "Recommendations on ESG Ratings (2023)"
            }
        ]
    }
]


def get_jurisdictions_by_priority(priority: str = "critical") -> list:
    """Get jurisdictions filtered by priority level"""
    return [j for j in COMPREHENSIVE_JURISDICTIONS if j.get("priority") == priority]


def get_jurisdictions_for_monthly_update() -> list:
    """Get jurisdictions that need monthly updates (ESG, Crypto focus)"""
    return [j for j in COMPREHENSIVE_JURISDICTIONS if j.get("update_frequency") == "monthly"]


def get_all_source_urls() -> list:
    """Get all unique URLs across all jurisdictions"""
    urls = []
    for jur in COMPREHENSIVE_JURISDICTIONS:
        for source in jur.get("sources", []):
            urls.append({
                "url": source["url"],
                "jurisdiction": jur["jurisdiction"],
                "regulator": jur["regulator"],
                "focus": source["focus"]
            })
    return urls


def format_for_scraper(jurisdiction_config: dict) -> list:
    """
    Convert jurisdiction config to format expected by scraper
    Returns list of individual URLs with metadata
    """
    formatted = []
    for source in jurisdiction_config.get("sources", []):
        formatted.append({
            "name": f"{jurisdiction_config['name']}-{source['type'].upper()}",
            "jurisdiction": jurisdiction_config["jurisdiction"],
            "regulator": jurisdiction_config["regulator"],
            "url": source["url"],
            "type": source["type"],
            "focus": source["focus"],
            "priority": jurisdiction_config["priority"],
            "update_frequency": jurisdiction_config["update_frequency"]
        })
    return formatted


def get_summary_stats() -> dict:
    """Get statistics about jurisdiction coverage"""
    stats = {
        "total_configs": len(COMPREHENSIVE_JURISDICTIONS),
        "total_sources": sum(len(j["sources"]) for j in COMPREHENSIVE_JURISDICTIONS),
        "by_jurisdiction": {},
        "by_priority": {"critical": 0, "high": 0, "medium": 0},
        "by_update_frequency": {}
    }
    
    for jur in COMPREHENSIVE_JURISDICTIONS:
        # Count by jurisdiction
        j_code = jur["jurisdiction"]
        stats["by_jurisdiction"][j_code] = stats["by_jurisdiction"].get(j_code, 0) + len(jur["sources"])
        
        # Count by priority
        priority = jur.get("priority", "medium")
        stats["by_priority"][priority] = stats["by_priority"].get(priority, 0) + 1
        
        # Count by update frequency
        freq = jur.get("update_frequency", "quarterly")
        stats["by_update_frequency"][freq] = stats["by_update_frequency"].get(freq, 0) + 1
    
    return stats


# Export for use in scraper
__all__ = [
    'COMPREHENSIVE_JURISDICTIONS',
    'get_jurisdictions_by_priority',
    'get_jurisdictions_for_monthly_update',
    'get_all_source_urls',
    'format_for_scraper',
    'get_summary_stats'
]

SOURCES_BY_JURISDICTION = {}
ALL_SOURCES = []

for jur_config in COMPREHENSIVE_JURISDICTIONS:
    jurisdiction = jur_config['jurisdiction']
    
    # Format sources for this jurisdiction
    sources = format_for_scraper(jur_config)
    
    # Add to ALL_SOURCES
    ALL_SOURCES.extend(sources)
    
    # Group by jurisdiction
    if jurisdiction not in SOURCES_BY_JURISDICTION:
        SOURCES_BY_JURISDICTION[jurisdiction] = []
    SOURCES_BY_JURISDICTION[jurisdiction].extend(sources)

# Priority sources (critical + high priority only)
PRIORITY_SOURCES = [s for s in ALL_SOURCES if s.get('priority') in ['critical', 'high']]

# Individual jurisdiction lists (for compatibility)
UK_SOURCES = SOURCES_BY_JURISDICTION.get('UK', [])
US_SOURCES = SOURCES_BY_JURISDICTION.get('US', [])
EU_SOURCES = SOURCES_BY_JURISDICTION.get('EU', [])
AU_SOURCES = SOURCES_BY_JURISDICTION.get('AU', [])
ZA_SOURCES = SOURCES_BY_JURISDICTION.get('ZA', [])
DE_SOURCES = SOURCES_BY_JURISDICTION.get('DE', [])
FR_SOURCES = SOURCES_BY_JURISDICTION.get('FR', [])
GLOBAL_SOURCES = SOURCES_BY_JURISDICTION.get('GLOBAL', [])

# Helper functions
def get_sources_for_jurisdiction(jurisdiction: str) -> list:
    """Get all sources for a jurisdiction"""
    return SOURCES_BY_JURISDICTION.get(jurisdiction.upper(), [])

def get_priority_sources(max_priority: str = 'critical') -> list:
    """Get sources by priority level"""
    priorities = {'critical': ['critical'], 'high': ['critical', 'high'], 'medium': ['critical', 'high', 'medium']}
    allowed = priorities.get(max_priority, ['critical', 'high'])
    return [s for s in ALL_SOURCES if s.get('priority') in allowed]
