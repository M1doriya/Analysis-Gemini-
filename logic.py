import json
import re
from datetime import datetime, timezone
from collections import defaultdict
from typing import Dict, List, Set, Tuple, Optional, Any

# ============================================================================
# CONSTANTS - UNIVERSAL BANKING RULES
# ============================================================================

BANK_CODES = {
    'AMFB': 'AmBank', 'AMB': 'AmBank', 'AMBANK': 'AmBank',
    'BIMB': 'Bank Islam', 'BANK ISLAM': 'Bank Islam',
    'MBB': 'Maybank', 'MAYBANK': 'Maybank',
    'RHB': 'RHB Bank',
    'PBB': 'Public Bank', 'PUBLIC BANK': 'Public Bank',
    'OCBC': 'OCBC Bank', 'HSBC': 'HSBC Bank',
    'UOB': 'UOB Bank', 'AFFIN': 'Affin Bank',
    'BSN': 'BSN', 'CITI': 'Citibank', 'SCB': 'Standard Chartered'
}

PROVIDED_BANK_CODES = {'CIMB', 'CIMBKL', 'CIMB14', 'CIMB9', 'CIMBSEK', 'HLB', 'HLBB', 'BMMB', 'MUAMALAT'}

INTER_ACCOUNT_MARKERS = [
    'ITB TRF', 'ITC TRF', 'INTERBANK', 'INTER ACC', 'OWN ACC', 
    'INTERCO TXN', 'INTER-CO', 'INTRA ACC', 'SELF TRF',
    'TR FROM CA', 'TR TO C/A'
]

STATUTORY_KEYWORDS = {
    'EPF/KWSP': ['KUMPULAN WANG SIMPANAN PEKERJA', 'KWSP', 'EPF', 'EMPLOYEES PROVIDENT'],
    'SOCSO/PERKESO': ['PERTUBUHAN KESELAMATAN SOSIAL', 'PERKESO', 'SOCSO', 'SOCIAL SECURITY'],
    'LHDN/Tax': ['LEMBAGA HASIL DALAM NEGERI', 'LHDN', 'PCB', 'MTD', 'CP39', 'CP38', 'INCOME TAX'],
    'HRDF/PSMB': ['PEMBANGUNAN SUMBER MANUSIA', 'HRDF', 'PSMB', 'HRD CORP']
}

SALARY_KEYWORDS = [
    'SALARY', 'GAJI', 'PAYROLL', 'WAGES', 'ALLOWANCE', 'ELAUN',
    'BONUS', 'COMMISSION', 'INCENTIVE', 'EPF EMPLOYER', 'STAFF CLAIM',
    'OVERTIME', 'OT CLAIM'
]

UTILITY_KEYWORDS = [
    'TNB', 'TENAGA NASIONAL', 'TENAGA', 
    'SYABAS', 'AIR SELANGOR', 'PENGURUSAN AIR', 'SAINS', 'SAJ', 'SAJH',
    'TELEKOM', 'TM NET', 'UNIFI', 'STREAMYX',
    'MAXIS', 'CELCOM', 'DIGI', 'U MOBILE', 'YES',
    'ASTRO', 'TIME DOTCOM', 'TIME FIBRE',
    'IWK', 'INDAH WATER'
]

BANK_CHARGE_KEYWORDS = [
    'SERVICE CHARGE', 'BANK CHARGE', 'AUTOPAY CHARGES', 'FEE', 
    'COMMISSION', 'STAMP DUTY', 'DUTI SETEM', 'COT', 
    'HANDLING CHARGE', 'PROCESSING FEE', 'ADM CHARGE', 'ADMIN FEE'
]

DISBURSEMENT_KEYWORDS = ['DISB', 'DISBURSEMENT', 'LOAN CR', 'FINANCING CR', 'DRAWDOWN', 'FACILITY RELEASE']
INTEREST_KEYWORDS = ['PROFIT PAID', 'PROFIT/HIBAH', 'HIBAH', 'INTEREST', 'DIVIDEND', 'FAEDAH', 'BONUS INTEREST']
REVERSAL_KEYWORDS = ['REVERSAL', 'REVERSE', 'REV', 'CANCELLED', 'VOID', 'RETURNED', 'REJECTED']

ROUND_FIGURE_THRESHOLD = 10000
ROUND_FIGURE_WARNING_PCT = 40

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def create_transaction_key(txn: Dict) -> Tuple:
    amount = txn.get('credit', 0) + txn.get('debit', 0)
    return (txn['date'], -amount, txn['description'])

def has_inter_account_marker(desc: str) -> bool:
    desc_upper = desc.upper()
    return any(marker in desc_upper for marker in INTER_ACCOUNT_MARKERS)

def has_company_name(desc: str, company_keywords: List[str]) -> bool:
    desc_upper = desc.upper()
    return any(kw.upper() in desc_upper for kw in company_keywords)

def get_missing_bank_code(desc: str, missing_codes: Set[str]) -> Optional[str]:
    desc_upper = desc.upper()
    for code in missing_codes:
        if code in desc_upper:
            return code
    return None

def is_round_figure(amount: float) -> bool:
    return amount >= ROUND_FIGURE_THRESHOLD and amount % 1000 == 0

def calculate_volatility(high: float, low: float) -> Tuple[float, str]:
    if high == low: return 0.0, 'LOW'
    avg = (high + low) / 2
    if avg == 0: return 0.0, 'LOW'
    swing = high - low
    vol_pct = (swing / avg) * 100
    
    if vol_pct <= 50: level = 'LOW'
    elif vol_pct <= 100: level = 'MODERATE'
    elif vol_pct <= 200: level = 'HIGH'
    else: level = 'EXTREME'
    
    return round(vol_pct, 2), level

def get_recurring_status(found_count: int, expected_count: int = 6) -> str:
    if found_count >= max(4, expected_count - 2): return 'FOUND'
    elif found_count >= 1: return 'PARTIAL'
    else: return 'NOT_FOUND'

def generate_related_party_patterns(related_parties: List[Dict]) -> List[Dict]:
    patterns = []
    stop_words = {'SDN', 'BHD', 'PLT', 'BERHAD', 'ENTERPRISE', 'TRADING', 
                  'SERVICES', 'SOLUTIONS', 'HOLDINGS', 'GROUP', 'AND', '&'}
    
    for rp in related_parties:
        name_upper = rp['name'].upper()
        words = [w for w in name_upper.split() if w not in stop_words and len(w) > 2]
        
        search_patterns = [name_upper]
        if len(words) >= 2:
            search_patterns.append(' '.join(words[:2]))
        if len(words) >= 1:
            search_patterns.append(words[0])
        
        patterns.append({
            'name': rp['name'],
            'relationship': rp['relationship'],
            'patterns': search_patterns
        })
    return patterns

def check_related_party(desc: str, rp_patterns: List[Dict]) -> Optional[Dict]:
    desc_upper = desc.upper()
    for rp in rp_patterns:
        for pattern in rp['patterns']:
            if pattern in desc_upper:
                purpose_note = ""
                for keyword in ['STATUTORY', 'SALARY', 'LOAN', 'PAYMENT', 'ADVANCE', 'INTERBANK']:
                    if keyword in desc_upper:
                        idx = desc_upper.find(keyword)
                        purpose_note = desc_upper[idx:idx+30].strip()
                        break
                return {
                    'name': rp['name'],
                    'relationship': rp['relationship'],
                    'purpose_note': purpose_note
                }
    return None

def check_statutory(desc: str) -> Optional[str]:
    desc_upper = desc.upper()
    for stat_type, keywords in STATUTORY_KEYWORDS.items():
        for keyword in keywords:
            if keyword in desc_upper:
                return stat_type
    return None

# ============================================================================
# MAIN ANALYSIS LOGIC
# ============================================================================

def process_analysis(
    company_name: str,
    company_keywords: List[str],
    related_parties: List[Dict],
    account_info: Dict,
    uploaded_data: Dict[str, Any]
) -> Dict:
    
    # Generate RP patterns
    rp_patterns = generate_related_party_patterns(related_parties)
    
    # 1. Combine Transactions
    all_transactions = []
    idx = 0
    
    for acc_id in sorted(account_info.keys()):
        if acc_id not in uploaded_data:
            continue
        
        # Safely get transactions list, handling potential empty/malformed data
        txns = uploaded_data[acc_id].get('transactions', [])
        
        for txn in txns:
            credit_amt = txn.get('credit', 0) or 0
            debit_amt = txn.get('debit', 0) or 0
            
            if credit_amt == 0 and debit_amt == 0:
                continue
            
            all_transactions.append({
                'idx': idx,
                'account_id': acc_id,
                'date': txn['date'],
                'description': txn['description'],
                'debit': debit_amt,
                'credit': credit_amt,
                'balance': txn.get('balance', 0) or 0,
                'category': None,
                'exclude_from_turnover': False,
                'is_related_party': False,
                'related_party_name': '',
                'related_party_relationship': '',
                'purpose_note': ''
            })
            idx += 1
            
    all_transactions.sort(key=create_transaction_key)
    for i, txn in enumerate(all_transactions):
        txn['sorted_idx'] = i
        
    # 2. Missing Bank Detection
    missing_accounts = defaultdict(int)
    for txn in all_transactions:
        desc_upper = txn['description'].upper()
        for code, name in BANK_CODES.items():
            if code in desc_upper and code not in PROVIDED_BANK_CODES:
                missing_accounts[f"{code} ({name})"] += 1
                
    missing_bank_codes = set([key.split()[0] for key in missing_accounts.keys()])

    # 3. Seperate Credits/Debits
    credits = [t for t in all_transactions if t['credit'] > 0]
    debits = [t for t in all_transactions if t['debit'] > 0]
    used_indices = set()
    
    # Lists for storage
    matched_transfers = []
    unverified_credit_transfers = []
    unverified_debit_transfers = []
    related_party_credits = []
    related_party_debits = []
    loan_disbursements = []
    interest_credits = []
    reversals = []
    genuine_credits = []
    statutory_payments = []
    salary_wages = []
    utilities = []
    bank_charges = []
    supplier_payments = []
    
    statutory_by_type = defaultdict(list)
    
    # Sorting for deterministic matching
    credits_sorted = sorted(credits, key=lambda x: (x['date'], -x['credit'], x['description']))
    debits_sorted = sorted(debits, key=lambda x: (x['date'], -x['debit'], x['description']))

    # 4. CREDIT CATEGORIZATION
    
    # Priority 1: Inter-Account Matched
    for credit_txn in credits_sorted:
        if credit_txn['sorted_idx'] in used_indices: continue
        
        for debit_txn in debits_sorted:
            if debit_txn['sorted_idx'] in used_indices: continue
            if debit_txn['account_id'] == credit_txn['account_id']: continue
            
            if abs(credit_txn['credit'] - debit_txn['debit']) > 1: continue
            
            c_date = datetime.strptime(credit_txn['date'], '%Y-%m-%d')
            d_date = datetime.strptime(debit_txn['date'], '%Y-%m-%d')
            if abs((c_date - d_date).days) > 1: continue
            
            c_desc = credit_txn['description'].upper()
            d_desc = debit_txn['description'].upper()
            
            has_marker = (has_inter_account_marker(c_desc) or has_inter_account_marker(d_desc) or
                         has_company_name(c_desc, company_keywords) or has_company_name(d_desc, company_keywords))
            
            if has_marker or credit_txn['credit'] >= 50000:
                matched_transfers.append({
                    'date': credit_txn['date'],
                    'amount': credit_txn['credit'],
                    'from_account': debit_txn['account_id'],
                    'to_account': credit_txn['account_id'],
                    'credit_description': credit_txn['description'],
                    'debit_description': debit_txn['description'],
                    'credit_idx': credit_txn['sorted_idx'],
                    'debit_idx': debit_txn['sorted_idx']
                })
                credit_txn['category'] = 'INTER_ACCOUNT_TRANSFER'
                credit_txn['exclude_from_turnover'] = True
                debit_txn['category'] = 'INTER_ACCOUNT_TRANSFER'
                debit_txn['exclude_from_turnover'] = True
                used_indices.add(credit_txn['sorted_idx'])
                used_indices.add(debit_txn['sorted_idx'])
                break

    # Priority 2: Unverified IA
    for credit_txn in credits_sorted:
        if credit_txn['sorted_idx'] in used_indices: continue
        desc_upper = credit_txn['description'].upper()
        missing_bank = get_missing_bank_code(desc_upper, missing_bank_codes)
        
        if missing_bank and (has_inter_account_marker(desc_upper) or has_company_name(desc_upper, company_keywords)):
            unverified_credit_transfers.append({
                'date': credit_txn['date'],
                'account': credit_txn['account_id'],
                'type': 'CREDIT',
                'amount': credit_txn['credit'],
                'description': credit_txn['description'],
                'target_bank': missing_bank,
                'verification_status': 'UNVERIFIED'
            })
            credit_txn['category'] = 'INTER_ACCOUNT_TRANSFER_UNVERIFIED'
            credit_txn['exclude_from_turnover'] = True
            used_indices.add(credit_txn['sorted_idx'])

    # Priority 3: Related Party
    for credit_txn in credits_sorted:
        if credit_txn['sorted_idx'] in used_indices: continue
        rp_match = check_related_party(credit_txn['description'], rp_patterns)
        if rp_match:
            credit_txn['category'] = 'RELATED_PARTY'
            credit_txn['exclude_from_turnover'] = True
            credit_txn['is_related_party'] = True
            credit_txn['related_party_name'] = rp_match['name']
            credit_txn['related_party_relationship'] = rp_match['relationship']
            credit_txn['purpose_note'] = rp_match['purpose_note']
            related_party_credits.append(credit_txn)
            used_indices.add(credit_txn['sorted_idx'])
            
    # Priority 4: Loan Disbursement
    for credit_txn in credits_sorted:
        if credit_txn['sorted_idx'] in used_indices: continue
        desc_upper = credit_txn['description'].upper()
        if any(kw in desc_upper for kw in DISBURSEMENT_KEYWORDS):
            loan_disbursements.append({'date': credit_txn['date'], 'amount': credit_txn['credit'], 'description': credit_txn['description']})
            credit_txn['category'] = 'LOAN_DISBURSEMENT'
            credit_txn['exclude_from_turnover'] = True
            used_indices.add(credit_txn['sorted_idx'])

    # Priority 5: Interest
    for credit_txn in credits_sorted:
        if credit_txn['sorted_idx'] in used_indices: continue
        desc_upper = credit_txn['description'].upper()
        if any(kw in desc_upper for kw in INTEREST_KEYWORDS):
            interest_credits.append({'date': credit_txn['date'], 'amount': credit_txn['credit'], 'description': credit_txn['description']})
            credit_txn['category'] = 'INTEREST_PROFIT_DIVIDEND'
            credit_txn['exclude_from_turnover'] = True
            used_indices.add(credit_txn['sorted_idx'])

    # Priority 6: Reversal
    for credit_txn in credits_sorted:
        if credit_txn['sorted_idx'] in used_indices: continue
        desc_upper = credit_txn['description'].upper()
        if any(kw in desc_upper for kw in REVERSAL_KEYWORDS):
            reversals.append({'date': credit_txn['date'], 'amount': credit_txn['credit'], 'description': credit_txn['description']})
            credit_txn['category'] = 'REVERSAL'
            credit_txn['exclude_from_turnover'] = True
            used_indices.add(credit_txn['sorted_idx'])

    # Priority 7: Genuine
    for credit_txn in credits_sorted:
        if credit_txn['sorted_idx'] in used_indices: continue
        genuine_credits.append({
            'date': credit_txn['date'],
            'amount': credit_txn['credit'],
            'description': credit_txn['description'],
            'account': credit_txn['account_id']
        })
        credit_txn['category'] = 'GENUINE_SALES_COLLECTIONS'
        credit_txn['exclude_from_turnover'] = False
        used_indices.add(credit_txn['sorted_idx'])

    # 5. DEBIT CATEGORIZATION
    # Priority 1 done (matched transfers)

    # Priority 2: Related Party
    for debit_txn in debits_sorted:
        if debit_txn['sorted_idx'] in used_indices: continue
        rp_match = check_related_party(debit_txn['description'], rp_patterns)
        if rp_match:
            debit_txn['category'] = 'RELATED_PARTY'
            debit_txn['exclude_from_turnover'] = True
            debit_txn['is_related_party'] = True
            debit_txn['related_party_name'] = rp_match['name']
            debit_txn['related_party_relationship'] = rp_match['relationship']
            debit_txn['purpose_note'] = rp_match['purpose_note']
            related_party_debits.append(debit_txn)
            used_indices.add(debit_txn['sorted_idx'])

    # Priority 3: Unverified IA
    for debit_txn in debits_sorted:
        if debit_txn['sorted_idx'] in used_indices: continue
        desc_upper = debit_txn['description'].upper()
        missing_bank = get_missing_bank_code(desc_upper, missing_bank_codes)
        if missing_bank and (has_inter_account_marker(desc_upper) or has_company_name(desc_upper, company_keywords)):
            unverified_debit_transfers.append({
                'date': debit_txn['date'],
                'account': debit_txn['account_id'],
                'type': 'DEBIT',
                'amount': debit_txn['debit'],
                'description': debit_txn['description'],
                'target_bank': debit_txn.get('target_bank', missing_bank),
                'verification_status': 'UNVERIFIED'
            })
            debit_txn['category'] = 'INTER_ACCOUNT_TRANSFER_UNVERIFIED'
            debit_txn['exclude_from_turnover'] = True
            used_indices.add(debit_txn['sorted_idx'])

    # Priority 4: Statutory
    for debit_txn in debits_sorted:
        if debit_txn['sorted_idx'] in used_indices: continue
        stat_type = check_statutory(debit_txn['description'])
        if stat_type:
            statutory_payments.append({
                'date': debit_txn['date'],
                'type': stat_type,
                'amount': debit_txn['debit'],
                'description': debit_txn['description'],
                'account': debit_txn['account_id']
            })
            statutory_by_type[stat_type].append(debit_txn['date'][:7])
            debit_txn['category'] = 'STATUTORY_PAYMENT'
            debit_txn['exclude_from_turnover'] = False
            used_indices.add(debit_txn['sorted_idx'])

    # Priority 5: Salary
    for debit_txn in debits_sorted:
        if debit_txn['sorted_idx'] in used_indices: continue
        desc_upper = debit_txn['description'].upper()
        if any(kw in desc_upper for kw in SALARY_KEYWORDS):
            salary_wages.append({'date': debit_txn['date'], 'amount': debit_txn['debit'], 'description': debit_txn['description']})
            debit_txn['category'] = 'SALARY_WAGES'
            debit_txn['exclude_from_turnover'] = False
            used_indices.add(debit_txn['sorted_idx'])

    # Priority 6: Utilities
    for debit_txn in debits_sorted:
        if debit_txn['sorted_idx'] in used_indices: continue
        desc_upper = debit_txn['description'].upper()
        if any(kw in desc_upper for kw in UTILITY_KEYWORDS):
            utilities.append({'date': debit_txn['date'], 'amount': debit_txn['debit'], 'description': debit_txn['description']})
            debit_txn['category'] = 'UTILITIES'
            debit_txn['exclude_from_turnover'] = False
            used_indices.add(debit_txn['sorted_idx'])

    # Priority 7: Bank Charges
    for debit_txn in debits_sorted:
        if debit_txn['sorted_idx'] in used_indices: continue
        desc_upper = debit_txn['description'].upper()
        if any(kw in desc_upper for kw in BANK_CHARGE_KEYWORDS) and debit_txn['debit'] < 1000:
            bank_charges.append({'date': debit_txn['date'], 'amount': debit_txn['debit'], 'description': debit_txn['description']})
            debit_txn['category'] = 'BANK_CHARGES'
            debit_txn['exclude_from_turnover'] = False
            used_indices.add(debit_txn['sorted_idx'])

    # Priority 8: Supplier (Default)
    for debit_txn in debits_sorted:
        if debit_txn['sorted_idx'] in used_indices: continue
        supplier_payments.append({'date': debit_txn['date'], 'amount': debit_txn['debit'], 'description': debit_txn['description']})
        debit_txn['category'] = 'SUPPLIER_VENDOR_PAYMENTS'
        debit_txn['exclude_from_turnover'] = False
        used_indices.add(debit_txn['sorted_idx'])

    # 6. TOTALS
    total_credits = sum(t['credit'] for t in all_transactions if t['credit'] > 0)
    total_debits = sum(t['debit'] for t in all_transactions if t['debit'] > 0)
    
    # Exclusions
    matched_credit_amount = sum(t['amount'] for t in matched_transfers)
    unverified_credit_amount = sum(t['amount'] for t in unverified_credit_transfers)
    rp_credit_amount = sum(t['credit'] for t in related_party_credits)
    loan_disb_amount = sum(t['amount'] for t in loan_disbursements)
    interest_amount = sum(t['amount'] for t in interest_credits)
    reversal_amount = sum(t['amount'] for t in reversals)
    
    total_credit_exclusions = (matched_credit_amount + unverified_credit_amount + 
                               rp_credit_amount + loan_disb_amount + 
                               interest_amount + reversal_amount)
    
    matched_debit_amount = matched_credit_amount
    unverified_debit_amount = sum(t['amount'] for t in unverified_debit_transfers)
    rp_debit_amount = sum(t['debit'] for t in related_party_debits)
    
    total_debit_exclusions = matched_debit_amount + unverified_debit_amount + rp_debit_amount
    
    net_credits = total_credits - total_credit_exclusions
    net_debits = total_debits - total_debit_exclusions

    # 7. METRICS AND SCORING
    
    # Account Summary Building
    accounts = []
    all_highs = []
    all_lows = []
    
    for acc_id in sorted(account_info.keys()):
        if acc_id not in uploaded_data: continue
        acc_data = uploaded_data[acc_id]
        info = account_info[acc_id]
        
        monthly = []
        # Fallback if monthly_summary missing
        m_summary = acc_data.get('monthly_summary', [])
        
        for m in m_summary:
            high = m.get('highest_balance', 0)
            low = m.get('lowest_balance', 0)
            vol_pct, vol_level = calculate_volatility(high, low)
            all_highs.append(high)
            all_lows.append(low)
            
            monthly.append({
                'month': m['month'],
                'month_name': datetime.strptime(m['month'], '%Y-%m').strftime('%B %Y'),
                'transaction_count': m.get('transaction_count', 0),
                'opening': round(m.get('ending_balance', 0) - m.get('net_change', 0), 2),
                'closing': m.get('ending_balance', 0),
                'credits': m.get('total_credit', 0),
                'debits': m.get('total_debit', 0),
                'volatility_level': vol_level
            })

        accounts.append({
            'account_id': acc_id,
            'bank_name': info['bank_name'],
            'account_number': info['account_number'],
            'monthly_summary': monthly
        })

    # Overall Volatility
    if all_highs and all_lows:
        overall_vol, overall_level = calculate_volatility(max(all_highs), min(all_lows))
    else:
        overall_vol, overall_level = 0, 'LOW'

    # Round Figure
    round_figure_credits = [t for t in genuine_credits if is_round_figure(t['amount'])]
    round_figure_total = sum(t['amount'] for t in round_figure_credits)
    round_figure_pct = (round_figure_total / total_credits * 100) if total_credits > 0 else 0

    # Recurring Payments (Months Calculation)
    all_dates = [t['date'] for t in all_transactions]
    expected_months = sorted(set(d[:7] for d in all_dates))
    num_months = len(expected_months) or 1
    
    epf_months = set(statutory_by_type.get('EPF/KWSP', []))
    socso_months = set(statutory_by_type.get('SOCSO/PERKESO', []))
    lhdn_months = set(statutory_by_type.get('LHDN/Tax', []))
    
    # 8. SCORING
    checks = [
        {'id': 1, 'name': 'Volatility Level', 'status': 'PASS' if overall_level not in ['HIGH', 'EXTREME'] else 'FAIL', 'points': 2 if overall_level not in ['HIGH', 'EXTREME'] else 0},
        {'id': 2, 'name': 'Round Figure %', 'status': 'PASS' if round_figure_pct <= ROUND_FIGURE_WARNING_PCT else 'FAIL', 'points': 2 if round_figure_pct <= ROUND_FIGURE_WARNING_PCT else 0},
        {'id': 3, 'name': 'EPF Payment', 'status': 'PASS' if len(epf_months) >= max(4, num_months - 2) else 'FAIL', 'points': 1 if len(epf_months) >= max(4, num_months - 2) else 0},
        {'id': 4, 'name': 'Data Completeness', 'status': 'FAIL' if missing_accounts else 'PASS', 'points': 0}
    ]
    
    score = sum(c['points'] for c in checks) # Simplified scoring for this demo version
    
    # 9. FINAL JSON STRUCTURE
    result = {
        'report_info': {
            'company_name': company_name,
            'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'total_months': num_months,
            'accounts_not_provided': [f"{k} ({v} txns)" for k, v in missing_accounts.items()]
        },
        'consolidated': {
            'gross': {'total_credits': total_credits, 'total_debits': total_debits},
            'business_turnover': {'net_credits': net_credits, 'net_debits': net_debits},
            'exclusions': {
                'credits': total_credit_exclusions,
                'debits': total_debit_exclusions,
                'breakdown': {
                    'related_party_credits': rp_credit_amount,
                    'inter_account_credits': matched_credit_amount + unverified_credit_amount,
                    'loan_disbursement': loan_disb_amount
                }
            }
        },
        'integrity_score': {
            'score': score, 
            'level': overall_level,
            'checks': checks
        },
        'related_party_transactions': {
             'summary': {'total_credits': rp_credit_amount, 'total_debits': rp_debit_amount},
             'details': [{'date': t['date'], 'party': t['related_party_name'], 'amount': t['credit'] if t['credit']>0 else t['debit'], 'type': 'CREDIT' if t['credit']>0 else 'DEBIT'} 
                         for t in sorted(related_party_credits + related_party_debits, key=lambda x: x['date'])]
        },
        'accounts': accounts
    }
    
    return result
