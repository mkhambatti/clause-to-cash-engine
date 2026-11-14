"""Service-category taxonomy for the (synthetic) vendor universe.

Thirty categories spanning the streaming stack, broadcast operations, content
operations and corporate services.  ``phrases`` are the three ways the
*seen* drafting styles describe the service; ``holdout`` is how the held-out
style (never used to write or tune any rule) describes it.

All vendor names produced from this table are fictional.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    unit: str  # single-token unit label used in price schedules
    unit_price: float  # tier-1 list price in USD
    spend_a: float  # annual spend (USD) of the larger legacy company in this category
    stream: bool  # part of the streaming-stack convergence (contends for the same engineers)
    switch_pct: float  # one-time switching cost as a fraction of annual spend
    mig_q: int  # minimum quarters of lead time before volume can move
    phrases: tuple[str, str, str]
    holdout: str
    noun: str  # used to build fictional vendor names


def _c(key, label, unit, price, spend_m, stream, sw, mq, phrases, holdout, noun) -> Category:  # type: ignore[no-untyped-def]
    return Category(key, label, unit, price, spend_m * 1e6, stream, sw, mq, phrases, holdout, noun)


CATEGORIES: list[Category] = [
    _c("cdn_delivery", "Content delivery (CDN)", "PB", 6500, 45, True, 0.06, 2,
       ("content delivery network (CDN) services", "video delivery and edge caching services", "CDN Delivery"),
       "pushing streams out to viewers from regional edge nodes", "Edge Networks"),
    _c("cloud_compute", "Cloud compute", "vCPU-hour", 0.041, 70, True, 0.12, 3,
       ("cloud computing services", "public cloud infrastructure services", "Cloud Compute"),
       "rented server capacity in the vendor's data centers", "Cloudworks"),
    _c("cloud_storage", "Cloud storage & archive", "PB-month", 21000, 18, True, 0.08, 2,
       ("cloud storage and archive services", "object storage and archival services", "Cloud Storage"),
       "somewhere to keep petabytes of masters and backups", "Archive Systems"),
    _c("transcoding", "Transcoding & packaging", "output-hour", 0.9, 12, True, 0.07, 2,
       ("video transcoding and packaging services", "media encoding and packaging services", "Transcoding"),
       "turning mezzanine files into streaming-ready renditions", "Encode"),
    _c("drm_licensing", "DRM licensing", "million-licenses", 230, 6, True, 0.15, 3,
       ("digital rights management (DRM) license services", "content protection and license delivery services", "DRM Licensing"),
       "issuing the keys that let paying viewers decrypt protected video", "Shield Technologies"),
    _c("ssai_ad_serving", "Ad insertion (SSAI)", "million-impressions", 41, 20, True, 0.08, 2,
       ("server-side ad insertion (SSAI) services", "ad decisioning and stitching services", "SSAI"),
       "stitching commercials into streams before they reach the player", "AdStitch"),
    _c("video_player_sdk", "Player SDK & QoE", "million-hours", 5200, 5, True, 0.10, 2,
       ("video player SDK and playback analytics services", "player software development kit and quality-of-experience analytics", "Player SDK"),
       "the playback engine embedded in our apps plus its telemetry", "Playback Labs"),
    _c("identity_auth", "Customer identity", "MAU-month", 0.021, 4, True, 0.14, 3,
       ("customer identity and authentication services", "consumer sign-in and identity management services", "Identity Platform"),
       "letting subscribers log in and manage accounts", "Identity Systems"),
    _c("push_messaging", "Messaging (push/email)", "million-messages", 95, 3, True, 0.05, 1,
       ("push notification and email messaging services", "customer messaging delivery services", "Messaging"),
       "nudging subscribers with alerts and newsletters", "Messaging"),
    _c("payments_processing", "Payments", "transaction", 0.31, 8, True, 0.12, 3,
       ("payment processing services", "subscription billing and payment gateway services", "Payments"),
       "charging subscribers' cards each month", "Payments"),
    _c("playout_mcr", "Playout & master control", "channel-month", 9500, 14, False, 0.10, 3,
       ("channel playout and master control services", "linear playout and origination services", "Playout"),
       "running our linear channels around the clock from their facility", "Broadcast Services"),
    _c("satellite_distribution", "Satellite distribution", "transponder-month", 41000, 22, False, 0.04, 2,
       ("satellite uplink and distribution services", "transponder capacity and affiliate distribution services", "Satellite Distribution"),
       "beaming our feeds up to orbit for affiliates", "Satcom"),
    _c("fiber_transport", "Live fiber transport", "circuit-month", 6200, 9, False, 0.05, 2,
       ("live video fiber transport services", "contribution and distribution network services", "Fiber Transport"),
       "carrying live game feeds over dedicated glass between venues and our hub", "Fiber Networks"),
    _c("closed_captioning", "Captioning & QC", "program-hour", 140, 4, False, 0.02, 1,
       ("closed captioning and quality control services", "caption creation and QC services", "Captioning"),
       "producing on-screen text for the hearing impaired and checking it", "Caption Services"),
    _c("localization_dubbing", "Dubbing & localization", "finished-minute", 85, 16, False, 0.03, 1,
       ("dubbing and localization services", "audio dubbing and language adaptation services", "Localization"),
       "re-voicing shows into other languages", "Language Studios"),
    _c("subtitling", "Subtitling & SDH", "finished-minute", 9, 6, False, 0.02, 1,
       ("subtitling and SDH services", "subtitle authoring and translation services", "Subtitling"),
       "on-screen translations of dialogue", "Subtitle Services"),
    _c("metadata_enrichment", "Content metadata", "title", 14, 3, False, 0.04, 1,
       ("content metadata enrichment services", "catalog metadata and tagging services", "Metadata Services"),
       "describing and tagging titles so viewers can find them", "Data Services"),
    _c("render_services", "VFX rendering", "core-hour", 0.062, 25, False, 0.05, 2,
       ("visual effects rendering services", "render farm and compute services for VFX", "VFX Rendering"),
       "crunching frames for effects shots on a big pool of cores", "Render Cloud"),
    _c("erp_saas", "ERP subscription", "user-month", 95, 14, False, 0.15, 4,
       ("enterprise resource planning (ERP) software-as-a-service", "cloud ERP subscription services", "ERP Subscription"),
       "the finance and procurement system of record, hosted by the vendor", "Enterprise Software"),
    _c("hris_saas", "HR & payroll", "employee-month", 14, 6, False, 0.12, 3,
       ("human resources and payroll software", "HRIS and payroll subscription services", "HR Platform"),
       "software that runs paychecks and employee records", "People Systems"),
    _c("crm_saas", "CRM subscription", "user-month", 110, 5, False, 0.10, 2,
       ("customer relationship management (CRM) software", "cloud CRM subscription services", "CRM Subscription"),
       "tracking advertiser and distributor relationships in the vendor's app", "Relationship Software"),
    _c("it_managed_services", "Managed IT", "ticket", 32, 30, False, 0.08, 2,
       ("managed IT and service desk services", "IT infrastructure support and helpdesk services", "Managed IT"),
       "keeping laptops, networks, and tickets handled by the vendor's staff", "IT Services"),
    _c("soc_services", "Managed security (SOC)", "endpoint-month", 4.8, 9, False, 0.08, 2,
       ("managed security operations center (SOC) services", "security monitoring and incident response services", "Security Operations"),
       "watching for intruders and responding when they show up", "Cyber Defense"),
    _c("endpoint_software", "Productivity software", "user-month", 28, 17, False, 0.05, 2,
       ("productivity and endpoint software licenses", "desktop productivity suite and device management licenses", "Productivity Software"),
       "office apps and device management for staff", "Workplace Software"),
    _c("ucaas_telephony", "Unified communications", "user-month", 17, 7, False, 0.06, 2,
       ("unified communications and telephony services", "business voice and collaboration services", "Unified Communications"),
       "phones and video meetings for employees", "Communications"),
    _c("travel_expense", "Travel & expense", "transaction", 3.2, 3, False, 0.06, 1,
       ("travel and expense management platform", "corporate travel booking and expense reporting services", "Travel & Expense"),
       "booking trips and reimbursing receipts", "Travel Systems"),
    _c("facility_security", "Facility security", "guard-hour", 38, 8, False, 0.03, 1,
       ("facility security guard services", "on-site physical security staffing services", "Security Guard Services"),
       "uniformed officers at our lots and offices", "Protective Services"),
    _c("temp_staffing", "Temporary staffing", "hour", 41, 40, False, 0.02, 1,
       ("temporary staffing services", "contingent workforce and staffing services", "Staffing"),
       "supplying short-term workers on demand", "Talent Solutions"),
    _c("ediscovery_hosting", "eDiscovery hosting", "GB-month", 14, 6, False, 0.05, 1,
       ("eDiscovery hosting and review services", "legal document hosting and review platform services", "eDiscovery"),
       "hosting documents for lawyers to search during litigation", "Legal Technologies"),
    _c("audience_research", "Audience data", "seat-year", 24000, 12, False, 0.04, 1,
       ("audience measurement and research data services", "viewership analytics and ratings data services", "Audience Data"),
       "numbers on who watched what", "Insights"),
]
CATEGORY_BY_KEY: dict[str, Category] = {c.key: c for c in CATEGORIES}
CATEGORY_KEYS: tuple[str, ...] = tuple(c.key for c in CATEGORIES)

VENDOR_PREFIXES = [
    "Kestrel", "Quillon", "Fenwick", "Ironbark", "Solstice", "Tamarack", "Vantor", "Brightwater",
    "Corvid", "Halden", "Marlow", "Northgate", "Opaline", "Pinecrest", "Sablewood", "Tidewater",
    "Umbra", "Valence", "Westmere", "Yarrow", "Arden", "Bexley", "Calloway", "Dunmore", "Elmsworth",
    "Farrow", "Garnet", "Hollis", "Ingram", "Juniper",
]
