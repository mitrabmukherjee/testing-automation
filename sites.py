"""Contact-page URLs to test."""

SITES = [
    "https://www.100percentaccuracy.ca/",
    "https://www.audiatranscription.com/",
    "https://www.campbelltranscription.com/",
    "https://www.dallascrosato.ca/",
    "https://www.danipeeltranscripts.ca/",
    "https://www.debrabrosetranscription.ca/",
    "https://www.deecoppingtranscripts.com/",
    "https://www.denisejensentranscription.ca/",
    "https://www.dtstranscription.ca/",
    "https://www.fostertranscriptionservices.com",
    "https://www.gabrielsetranscription.ca/",
    "https://www.hmillertranscription.com/",
    "https://www.jdtranscripts.com/",
    "https://www.jpantaleontranscripts.com/",
    "https://www.loripower.ca/",
    "https://www.margaretgrahamtranscription.com/",
    "https://www.maxwelltranscriptions.ca/",
    "https://www.ndtranscription.com/",
    "https://www.shelbywiltshire.ca/",
    "https://www.sl-nstranscripts.ca/",
    "https://www.somervilletranscription.ca/",
    "https://www.taniatranscripts.ca/",
    "https://www.transcriptsbyhilda.com/",
    "https://www.verbatimlegal.ca/",
]


def contact_url(site: str) -> str:
    return site.rstrip("/") + "/contact"
