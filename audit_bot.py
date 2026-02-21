"""
Legislative Metadata Audit Bot
Automated XML validation pipeline for the unitedstates/congress repository.
Audits bill metadata integrity, generates structured JSON reports, and
posts actionable summaries as GitHub Actions step summaries.
"""

import os
import xml.etree.ElementTree as ET
import json
import sys
import datetime
import argparse
from collections import defaultdict
from pathlib import Path

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────
REQUIRED_FIELDS = {
    'billType':       './/billType',
    'billNumber':     './/billNumber',
    'congress':       './/congress',
    'originChamber':  './/originChamber',
    'title':          './/title',
    'introducedDate': './/introducedDate',
}

OPTIONAL_BUT_AUDITED = {
    'sponsors':       './/sponsors/item',
    'committees':     './/committees/item',
    'actions':        './/actions/item',
    'policyArea':     './/policyArea/name',
    'cosponsors':     './/cosponsors/item',
}

VALID_BILL_TYPES   = {'HR', 'S', 'HJRES', 'SJRES', 'HCONRES', 'SCONRES', 'HRES', 'SRES'}
VALID_CHAMBERS     = {'House', 'Senate'}
DATE_FORMAT        = '%Y-%m-%d'
MAX_ANOMALIES_DISPLAY = 50


# ─────────────────────────────────────────────
# CORE AUDIT LOGIC
# ─────────────────────────────────────────────
def audit_bill_metadata(xml_path: str) -> dict:
    """
    Parses a US Congress bill XML file and validates metadata integrity.
    Returns a structured result dict with severity-tagged anomalies.
    """
    result = {
        'file':      xml_path,
        'status':    'pass',
        'critical':  [],
        'warnings':  [],
        'info':      [],
        'bill_id':   None,
    }

    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except ET.ParseError as e:
        result['critical'].append(f"XML Parse Error: {e}")
        result['status'] = 'critical'
        return result
    except Exception as e:
        result['critical'].append(f"Unexpected read error: {e}")
        result['status'] = 'critical'
        return result

    # ── Required field presence ──
    field_values = {}
    for field_name, xpath in REQUIRED_FIELDS.items():
        node = root.find(xpath)
        if node is None or not (node.text or '').strip():
            result['critical'].append(f"Missing required field: <{field_name}>")
        else:
            field_values[field_name] = node.text.strip()

    # ── Build a human-readable bill ID for reporting ──
    if 'billType' in field_values and 'billNumber' in field_values and 'congress' in field_values:
        result['bill_id'] = f"{field_values['congress']}-{field_values['billType']}-{field_values['billNumber']}"

    # ── Controlled vocabulary validation ──
    if 'billType' in field_values:
        if field_values['billType'].upper() not in VALID_BILL_TYPES:
            result['warnings'].append(
                f"Unrecognized billType '{field_values['billType']}' — expected one of {VALID_BILL_TYPES}"
            )

    if 'originChamber' in field_values:
        if field_values['originChamber'] not in VALID_CHAMBERS:
            result['warnings'].append(
                f"Unrecognized originChamber '{field_values['originChamber']}'"
            )

    # ── Date format validation ──
    if 'introducedDate' in field_values:
        try:
            datetime.datetime.strptime(field_values['introducedDate'], DATE_FORMAT)
        except ValueError:
            result['warnings'].append(
                f"introducedDate '{field_values['introducedDate']}' does not match expected format YYYY-MM-DD"
            )

    # ── Congress number sanity check ──
    if 'congress' in field_values:
        try:
            congress_num = int(field_values['congress'])
            if not (93 <= congress_num <= 120):  # reasonable modern range
                result['warnings'].append(
                    f"Congress number {congress_num} outside expected range (93–120)"
                )
        except ValueError:
            result['warnings'].append(f"Non-integer congress value: '{field_values['congress']}'")

    # ── Optional field audit ──
    sponsors = root.findall(OPTIONAL_BUT_AUDITED['sponsors'])
    if not sponsors:
        result['warnings'].append("No sponsors found — potential data orphan or unintroduced bill")
    else:
        # Validate each sponsor has required sub-fields
        for i, sponsor in enumerate(sponsors):
            bio_id = sponsor.find('bioguideId')
            name   = sponsor.find('fullName')
            if bio_id is None or not (bio_id.text or '').strip():
                result['warnings'].append(f"Sponsor[{i}] missing bioguideId — cross-reference broken")
            if name is None or not (name.text or '').strip():
                result['warnings'].append(f"Sponsor[{i}] missing fullName")

    committees = root.findall(OPTIONAL_BUT_AUDITED['committees'])
    if not committees:
        result['info'].append("No committee referrals recorded")

    actions = root.findall(OPTIONAL_BUT_AUDITED['actions'])
    if not actions:
        result['warnings'].append("No legislative actions recorded — bill may be a stub")

    policy_area = root.find(OPTIONAL_BUT_AUDITED['policyArea'])
    if policy_area is None or not (policy_area.text or '').strip():
        result['info'].append("No policyArea classification — reduces discoverability")

    # ── Set final status ──
    if result['critical']:
        result['status'] = 'critical'
    elif result['warnings']:
        result['status'] = 'warning'

    return result


# ─────────────────────────────────────────────
# REPORT GENERATION
# ─────────────────────────────────────────────
def generate_report(results: list, output_path: str = 'audit_report.json') -> dict:
    """Generates a structured JSON audit report and GitHub Actions step summary."""

    summary = {
        'generated_at':   datetime.datetime.utcnow().isoformat() + 'Z',
        'total_files':    len(results),
        'passed':         sum(1 for r in results if r['status'] == 'pass'),
        'warnings':       sum(1 for r in results if r['status'] == 'warning'),
        'critical':       sum(1 for r in results if r['status'] == 'critical'),
        'anomaly_types':  defaultdict(int),
    }

    # Aggregate anomaly type frequency
    for r in results:
        for msg in r['critical'] + r['warnings']:
            # Extract the anomaly category (first clause before colon or dash)
            key = msg.split(':')[0].split('—')[0].strip()
            summary['anomaly_types'][key] += 1

    summary['anomaly_types'] = dict(summary['anomaly_types'])

    full_report = {
        'summary': summary,
        'results': [r for r in results if r['status'] != 'pass'],  # only flagged files
    }

    with open(output_path, 'w') as f:
        json.dump(full_report, f, indent=2)

    return summary


def write_github_summary(summary: dict, all_anomalies: list):
    """Writes a formatted markdown summary to GitHub Actions step summary."""
    github_summary_path = os.environ.get('GITHUB_STEP_SUMMARY')
    if not github_summary_path:
        return

    lines = [
        "## 🏛️ Legislative Metadata Audit Report\n",
        f"| Metric | Count |",
        f"|--------|-------|",
        f"| Total Files Audited | {summary['total_files']} |",
        f"| ✅ Passed | {summary['passed']} |",
        f"| ⚠️ Warnings | {summary['warnings']} |",
        f"| 🚨 Critical | {summary['critical']} |",
        "",
        "### Most Common Anomaly Types",
    ]

    sorted_anomalies = sorted(summary['anomaly_types'].items(), key=lambda x: x[1], reverse=True)
    for anomaly_type, count in sorted_anomalies[:10]:
        lines.append(f"- `{anomaly_type}` — {count} occurrence(s)")

    if all_anomalies:
        lines += ["", "### Sample Critical Issues (first 20)", "```"]
        for msg in all_anomalies[:20]:
            lines.append(msg)
        lines.append("```")

    lines += [
        "",
        f"*Audit completed at {summary['generated_at']} UTC · audit_report.json artifact attached*"
    ]

    with open(github_summary_path, 'w') as f:
        f.write('\n'.join(lines))


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='Legislative Metadata Audit Bot')
    parser.add_argument('--data-dir',    default='./data',          help='Root directory containing XML files')
    parser.add_argument('--report-out',  default='audit_report.json', help='Output path for JSON report')
    parser.add_argument('--strict',      action='store_true',        help='Exit 1 on warnings (not just critical)')
    parser.add_argument('--max-files',   type=int, default=None,     help='Cap number of files audited (for testing)')
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        print(f"❌ Data directory '{data_dir}' not found.")
        sys.exit(1)

    print(f"🔍 Scanning {data_dir} for XML files...")
    xml_files = list(data_dir.rglob('*.xml'))

    if args.max_files:
        xml_files = xml_files[:args.max_files]

    if not xml_files:
        print("⚠️  No XML files found. Check your data directory structure.")
        sys.exit(0)

    print(f"📋 Found {len(xml_files):,} XML files. Beginning audit...\n")

    results      = []
    all_critical = []
    all_warnings = []

    for i, xml_path in enumerate(xml_files, 1):
        result = audit_bill_metadata(str(xml_path))
        results.append(result)

        for msg in result['critical']:
            all_critical.append(f"[CRITICAL] {result['bill_id'] or xml_path}: {msg}")
        for msg in result['warnings']:
            all_warnings.append(f"[WARNING]  {result['bill_id'] or xml_path}: {msg}")

        # Progress indicator every 500 files
        if i % 500 == 0:
            print(f"  ...processed {i:,} / {len(xml_files):,} files")

    # Generate report
    summary = generate_report(results, args.report_out)
    write_github_summary(summary, all_critical)

    # Console output
    print("\n" + "═" * 60)
    print("  LEGISLATIVE METADATA AUDIT COMPLETE")
    print("═" * 60)
    print(f"  Total Files:  {summary['total_files']:,}")
    print(f"  ✅ Passed:    {summary['passed']:,}")
    print(f"  ⚠️  Warnings:  {summary['warnings']:,}")
    print(f"  🚨 Critical:  {summary['critical']:,}")
    print(f"  Report:       {args.report_out}")
    print("═" * 60)

    if all_critical[:MAX_ANOMALIES_DISPLAY]:
        print(f"\n🚨 Critical Issues (showing first {MAX_ANOMALIES_DISPLAY}):")
        for msg in all_critical[:MAX_ANOMALIES_DISPLAY]:
            print(f"  {msg}")

    if all_warnings[:10]:
        print(f"\n⚠️  Sample Warnings:")
        for msg in all_warnings[:10]:
            print(f"  {msg}")

    # Exit code logic
    if summary['critical'] > 0:
        print(f"\n❌ Audit FAILED — {summary['critical']} critical issue(s) detected.")
        sys.exit(1)
    elif args.strict and summary['warnings'] > 0:
        print(f"\n❌ Strict mode: audit FAILED — {summary['warnings']} warning(s) detected.")
        sys.exit(1)
    else:
        print("\n✅ Audit passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()