import os
import xml.etree.ElementTree as ET
import json
import sys

def audit_bill_metadata(xml_path):
    """Parses US Congress Bill XML and validates metadata integrity."""
    anomalies = []
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        
        # Check for critical metadata fields
        bill_type = root.find('.//billType')
        bill_number = root.find('.//billNumber')
        sponsors = root.findall('.//sponsors/item')
        
        if bill_type is None or not bill_type.text:
            anomalies.append(f"Missing billType in {xml_path}")
        if bill_number is None or not bill_number.text:
            anomalies.append(f"Missing billNumber in {xml_path}")
        if not sponsors:
            anomalies.append(f"No sponsors found in {xml_path} - potential data orphan.")
            
    except ET.ParseError as e:
        anomalies.append(f"XML Parse Error in {xml_path}: {e}")
        
    return anomalies

def main():
    data_dir = "./data" # Assuming the unitedstates/congress bulk data structure
    all_anomalies = []
    
    for root_dir, _, files in os.walk(data_dir):
        for file in files:
            if file.endswith('.xml'):
                path = os.path.join(root_dir, file)
                issues = audit_bill_metadata(path)
                all_anomalies.extend(issues)
                
    if all_anomalies:
        print("🚨 Metadata Anomalies Detected:")
        for a in all_anomalies:
            print(f"- {a}")
        # Exit with error code to fail the GitHub Action if strictly enforcing
        sys.exit(1)
    else:
        print("✅ All legislative metadata passed the audit.")

if __name__ == "__main__":
    main()