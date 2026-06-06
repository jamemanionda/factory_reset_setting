<img width="566" height="329" alt="image" src="https://github.com/user-attachments/assets/c4d02a5e-75f9-4648-bf1b-d5391d43cfca" />

# Factory Reset Artifact Analyzer

A PyQt-based forensic GUI tool for analyzing Android factory-reset artifacts and estimating reset time with cross-validation.

- Input sources: `ZIP`, `Folder`, `ADB`
- Main script: `factory4.py`
- Saved results: `saved_results/*.json`

## Key Features

### 1) Artifact Analysis
- Automatically parses factory-reset-related artifacts
- Normalizes and displays timestamps (UTC/KST toggle)
- Provides consolidated timeline view in the Summary tab

### 2) Multi-Anchor Cross-Validation
- Evaluates event-level anchors and assigns confidence grades:
  - `Primary`
  - `Corroborative`
  - `Investigative Lead`
- Uses system-anchor cluster width (default: 30 minutes)
- Shows `T_reset` point estimate from the earliest consistent anchor

### 3) Visual Time Distribution
- Cross-validation panel includes an **anchor distribution table**
  - Component / Tier / Time / Offset (min) / Distribution bar
- Offset bars are rendered relative to the earliest anchor
- Includes 5-minute bucket summary for quick spread inspection

### 4) Extended Bootstat Analysis
- `factory_reset`
- `factory_reset_record_value`
- `factory_reset_current_time`
- `last_boot_time_utc`
- `build_date`

Also validates the co-updated pair: `factory_reset ↔ factory_reset_record_value` (`|Δ|` check).

### 5) Enhanced Recovery/Last_log Timeline
- Reconstructs absolute times using `get_system_time`
- Tracks wipe-related events:
  - `-- Wiping data`
  - `Wiping /data`
  - `Data wipe complete`
- Parses `reason / requested_time / caller` for local/remote trigger hints

### 6) Saved Results Management
- Auto-saves analysis results as JSON
- Supports filtering and selection-based browsing
- Batch export: **Excel / CSV folder / ZIP**

## Supported Artifacts

- `1` bootstat
- `21` recovery.log
- `22` last_log
- `3` suggestions.xml / setup_wizard_info.xml
- `4` persistent_properties
- `5` appops
- `6` wellbeing
- `7` internal (MediaProvider)
- `8` eRR.p
- `9` ULR_PERSISTENT_PREFS.xml

## Requirements

- Python 3.8+
- Windows / Linux / macOS
- (Optional) Android Platform Tools for ADB mode

## Installation

```bash
git clone <repo-url>
cd factory_reset_setting
python -m venv .venv
```

Windows:
```bash
.venv\Scripts\activate
```

Linux/macOS:
```bash
source .venv/bin/activate
```

Install dependencies:
```bash
pip install -r requirements.txt
```

## Run

```bash
python factory4.py
```

## Basic Workflow

1. Select source (`ZIP` / `ADB` / `Folder`)
2. Select artifacts (or choose All)
3. Click `Run Analysis`
4. Review Summary and artifact-specific tabs
5. Check confidence grade and distribution in Cross-Validation panel
6. Optionally run `Deep Search` and batch export saved results

## Output Files and Folders

- `saved_results/`: Saved analysis JSON files
- `saved_results_sort_settings.json`: Saved sort preferences
- `confirmed_time_settings.json`: Confirmed time settings
- `logs/`: Runtime logs
- `crash_dump.log`: Crash dump output

## Troubleshooting

### App does not start
- Reinstall dependencies:
  - `pip install -r requirements.txt`
- Verify Python version

### ADB mode issues
- Check connection with `adb devices`
- Enable USB debugging on the target device

### Timestamp looks inconsistent
- Verify UTC/KST toggle in the UI
- Check offset bars and cluster width in the cross-validation panel

## Security and Legal Notice

- All analysis runs locally on your machine.
- Use only on data/devices you are legally authorized to analyze.

## License

Follow the repository owner's licensing policy.
