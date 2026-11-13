"""Single source of truth for carrier bots and tracking-report folders."""

from pathlib import Path


BOT_ROOT = Path(__file__).resolve().parent / "carrier_bots"

CARRIERS = {
    "Evergreen": BOT_ROOT / "Evergreen" / "evergreen_tracking_bot.py",
    "ONE Line": BOT_ROOT / "ONE Line" / "one_line_tracking_bot.py",
    "Wan Hai": BOT_ROOT / "Wan Hai" / "wanhai_tracking_bot.py",
    "COSCO": BOT_ROOT / "COSCO" / "cosco_tracking_bot.py",
    "MSC": BOT_ROOT / "MSC" / "msc_tracking_bot.py",
    "CMA CGM": BOT_ROOT / "CMA CGM" / "cma_cgm_tracking_bot.py",
    "Maersk": BOT_ROOT / "Maersk" / "maersk_tracking_bot.py",
}

# Temporary automatic-run exclusion. Manual carrier buttons remain available.
AUTO_TRACKING_DISABLED = {"CMA CGM"}

# Trico is a single supplementary Colombo berthing-page read after carrier
# tracking.  It is deliberately not in CARRIERS/BOT_ORDER because it does not
# replace a carrier schedule and its ETB must never be treated as ETA or ATA.
TRICO_SCRIPT = BOT_ROOT / "Trico" / "trico_berthing_bot.py"

CARRIER_REPORTS_DIR = Path.home() / "Documents" / "ActivTrack Reports" / "Carrier Tracking Reports"
