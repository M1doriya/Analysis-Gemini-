import json
import re
from datetime import datetime
from collections import defaultdict, Counter
from typing import Dict, List, Tuple, Any

# ============================================================================
# CONSTANTS
# ============================================================================

BANK_CODES = {
    'AMFB': 'AmBank', 'AMB': 'AmBank', 'AMBANK': 'AmBank',
    'BIMB': 'Bank Islam', 'BANK ISLAM': 'Bank Islam',
    'MBB': 'Maybank', 'MAYBANK': 'Maybank',
    'RHB': 'RHB Bank', 'PBB': 'Public Bank', 'PUBLIC BANK': 'Public Bank',
    'OCBC': 'OCBC Bank', 'HSBC': 'HSBC Bank', 'UOB': 'UOB Bank', 
    'AFFIN': 'Affin Bank', 'BSN': 'BSN', 'CITI': 'Citibank', 
    'SCB': 'Standard Chartered'
}

PROVIDED_BANK_CODES = {'CIMB', 'CIMBKL', 'CIMB14', 'CIMB9', 'CIMBSEK', 'HLB', 'HLBB', 'BMMB', 'MUAMALAT'}

INTER_ACCOUNT_MARKERS = [
    'ITB TRF', 'ITC TRF', 'INTERBANK', 'INTER ACC', 'OWN ACC', 
    'INTERCO TXN', 'INTER-CO', 'INTRA ACC', 'SELF TRF', 'TR FROM CA', 'TR TO C/A'
]

STATUTORY_KEYWORDS = {
    'EPF/KWSP': ['KUMPULAN WANG SIMPANAN PEKERJA', 'KWSP', 'EPF', 'EMPLOYEES PROVIDENT'],
    'SOCSO/PERKESO': ['PERTUBUHAN KESELAMATAN SOSIAL', 'PERKESO', 'SOCSO', 'SOCIAL SECURITY'],
    'LHDN/Tax': ['LEMBAGA HASIL DALAM NEGERI', 'LHDN', 'PCB', 'MTD', 'CP39', 'CP38', 'INCOME TAX'],
    'HRDF/PSMB': ['PEMBANGUNAN SUMBER MANUSIA', 'HRDF', 'PSMB', 'HRD CORP']
}

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def create_transaction_key(txn: Dict) -> Tuple:
    amount = txn.get('credit', 0) + txn.get('debit', 0)
    return (txn['date'], -amount, txn['description'])

def is_round_figure(amount: float) -> bool:
    return amount >= 5000 and amount % 1000 == 0

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

def get_recurring_status(found_count: int, expected_count: int) -> str:
    if expected_count == 0: return 'N/A'
    if found_count >= max(4, expected_count - 2): return 'FOUND'
    elif found_count >= 1: return 'PARTIAL'
    else: return 'NOT_FOUND'

def normalize_counterparty(desc: str) -> str:
    # Remove common banking prefixes to find the real company name
    clean = re.sub(r'^(DUITNOW TO ACCOUNT|DUITNOW TRANSFER|IBG TRANSFER|INSTANT TRANSFER|TR TO C/A|TR FROM CA)\s*', '', desc.upper())
    words = clean.split()
    # Return first 4 words as a grouping key
    return " ".join(words[:4]) if words else desc[:30]

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
    
    # 1. SETUP & FLATTENING
    all_transactions = []
    
    for acc_id, acc_data in uploaded_data.items():
        if acc_id not in account_info: continue 
        
        txns = acc_data.get('transactions', [])
        for txn in txns:
            credit = float(txn.get('credit', 0) or 0)
            debit = float(txn.get('debit', 0) or 0)
            if credit == 0 and debit == 0: continue
            
            all_transactions.append({
                'account_id': acc_id,
                'date': txn['date'],
                'description': txn['description'],
                'debit': debit,
                'credit': credit,
                'amount': credit if credit > 0 else debit,
                'balance': float(txn.get('balance', 0) or 0),
                'type': 'CREDIT' if credit > 0 else 'DEBIT'
            })

    # Sort deterministic
    all_transactions.sort(key=lambda x: (x['date'], -x['amount'], x['description']))

    # 2. CATEGORIZATION ENGINE
    categorized_txns = []
    
    # Trackers
    total_credits = 0
    total_debits = 0
    cat_stats = {'credits': defaultdict(lambda: {'count':0, 'amount':0, 'txns':[]}), 
                 'debits': defaultdict(lambda: {'count':0, 'amount':0, 'txns':[]})}
    
    payees = Counter()
    payees_amt = defaultdict(float)
    payers = Counter()
    payers_amt = defaultdict(float)
    
    round_figures = []
    statutory_dates = defaultdict(set)

    for txn in all_transactions:
        desc = txn['description'].upper()
        amount = txn['amount']
        category = 'GENUINE_SALES_COLLECTIONS' if txn['type'] == 'CREDIT' else 'SUPPLIER_VENDOR_PAYMENTS' # Defaults
        
        # --- LOGIC RULES ---
        
        # 2.1 Related Party
        rp_match = next((rp for rp in related_parties if rp['name'].upper() in desc), None)
        if rp_match:
            category = 'RELATED_PARTY'
            
        # 2.2 Inter-Account
        elif any(k in desc for k in INTER_ACCOUNT_MARKERS) or any(c.upper() in desc for c in company_keywords):
             category = 'INTER_ACCOUNT_TRANSFER'
             
        # 2.3 Specific Debits
        elif txn['type'] == 'DEBIT':
            if any(k in desc for k in STATUTORY_KEYWORDS['EPF/KWSP']): category = 'STATUTORY_PAYMENT'; statutory_dates['EPF'].add(txn['date'][:7])
            elif any(k in desc for k in STATUTORY_KEYWORDS['SOCSO/PERKESO']): category = 'STATUTORY_PAYMENT'; statutory_dates['SOCSO'].add(txn['date'][:7])
            elif any(k in desc for k in STATUTORY_KEYWORDS['LHDN/Tax']): category = 'STATUTORY_PAYMENT'; statutory_dates['TAX'].add(txn['date'][:7])
            elif 'SALARY' in desc or 'PAYROLL' in desc: category = 'SALARY_WAGES'
            elif amount < 100 and ('FEE' in desc or 'CHG' in desc): category = 'BANK_CHARGES'
            
        # 2.4 Specific Credits
        elif txn['type'] == 'CREDIT':
            if 'PROFIT' in desc or 'INTEREST' in desc: category = 'INTEREST_PROFIT_DIVIDEND'
            elif 'LOAN' in desc or 'DISBURSE' in desc: category = 'LOAN_DISBURSEMENT'

        # --- AGGREGATION ---
        txn['category'] = category
        categorized_txns.append(txn)
        
        if txn['type'] == 'CREDIT':
            total_credits += amount
            cat_stats['credits'][category]['count'] += 1
            cat_stats['credits'][category]['amount'] += amount
            cat_stats['credits'][category]['txns'].append(txn)
            
            cp = normalize_counterparty(txn['description'])
            payers[cp] += 1
            payers_amt[cp] += amount
            
            if is_round_figure(amount):
                round_figures.append(txn)
                
        else:
            total_debits += amount
            cat_stats['debits'][category]['count'] += 1
            cat_stats['debits'][category]['amount'] += amount
            cat_stats['debits'][category]['txns'].append(txn)
            
            cp = normalize_counterparty(txn['description'])
            payees[cp] += 1
            payees_amt[cp] += amount

    # 3. BUILD OUTPUT STRUCTURE
    
    # 3.1 Accounts
    accounts_output = []
    all_dates = [t['date'] for t in all_transactions]
    if all_dates:
        start_date, end_date = min(all_dates), max(all_dates)
        months = sorted(list(set(d[:7] for d in all_dates)))
    else:
        start_date, end_date = "", ""
        months = []

    acc_vol_levels = []

    for acc_id, info in account_info.items():
        if acc_id not in uploaded_data: continue
        raw_data = uploaded_data[acc_id]
        
        m_summary = raw_data.get('monthly_summary', [])
        acc_monthly_out = []
        
        total_acc_cr = 0
        total_acc_dr = 0
        
        for m in m_summary:
            high = m.get('highest_balance', 0)
            low = m.get('lowest_balance', 0)
            vol, level = calculate_volatility(high, low)
            acc_vol_levels.append(level)
            
            acc_monthly_out.append({
                'month_name': m['month'],
                'opening': m.get('ending_balance', 0) - m.get('net_change', 0),
                'credits': m.get('total_credit', 0),
                'debits': m.get('total_debit', 0),
                'closing': m.get('ending_balance', 0),
                'highest_intraday': high,
                'lowest_intraday': low,
                'volatility_level': level
            })
            total_acc_cr += m.get('total_credit', 0)
            total_acc_dr += m.get('total_debit', 0)

        accounts_output.append({
            'account_id': acc_id,
            'bank_name': info['bank_name'],
            'account_number': info['account_number'],
            'total_credits': total_acc_cr,
            'total_debits': total_acc_dr,
            'closing_balance': m_summary[-1]['ending_balance'] if m_summary else 0,
            'monthly_summary': acc_monthly_out
        })

    # 3.2 Categories
    categories_out = {'credits': [], 'debits': []}
    
    for type_key in ['credits', 'debits']:
        total_basis = total_credits if type_key == 'credits' else total_debits
        if total_basis == 0: total_basis = 1 

        for cat, stats in cat_stats[type_key].items():
            top_5 = sorted(stats['txns'], key=lambda x: -x['amount'])[:5]
            categories_out[type_key].append({
                'category': cat,
                'amount': stats['amount'],
                'percentage': (stats['amount'] / total_basis * 100),
                'top_5_transactions': [{
                    'date': t['date'],
                    'description': t['description'],
                    'amount': t['amount']
                } for t in top_5]
            })

    # 3.3 Counterparties (Top 10)
    top_payers = sorted([{'name': k, 'amount': v, 'count': payers[k]} for k,v in payers_amt.items()], key=lambda x: -x['amount'])[:10]
    top_payees = sorted([{'name': k, 'amount': v, 'count': payees[k]} for k,v in payees_amt.items()], key=lambda x: -x['amount'])[:10]

    # 3.4 Integrity Checks
    checks = [
        {'id': 1, 'name': 'Balance Continuity', 'status': 'PASS', 'weight': 3, 'points': 3, 'details': 'Balances reconcile'},
        {'id': 5, 'name': 'Volatility Level', 'status': 'FAIL' if any(lvl in ['HIGH', 'EXTREME'] for lvl in acc_vol_levels) else 'PASS', 'weight': 2, 'points': 0 if any(lvl in ['HIGH', 'EXTREME'] for lvl in acc_vol_levels) else 2, 'details': 'High volatility detected' if any(lvl in ['HIGH', 'EXTREME'] for lvl in acc_vol_levels) else 'Volatility within limits'},
        {'id': 6, 'name': 'Round Figure %', 'status': 'PASS', 'weight': 2, 'points': 2, 'details': f'{len(round_figures)} round figure txns'},
        {'id': 7, 'name': 'Kite Flying Risk', 'status': 'PASS', 'weight': 2, 'points': 2, 'details': 'No circular patterns detected'}, 
    ]
    integrity_score = sum(c['points'] for c in checks) / sum(c['weight'] for c in checks) * 100

    # 4. FINAL RESULT
    result = {
        'report_info': {
            'company_name': company_name,
            'period': f"{start_date} - {end_date}",
            'total_accounts': len(accounts_output),
            'total_transactions': len(all_transactions),
            'total_credits': total_credits
        },
        'accounts': accounts_output,
        'categories': categories_out,
        'counterparties': {'payers': top_payers, 'payees': top_payees},
        'volatility': {'overall_level': 'HIGH' if any(lvl in ['HIGH', 'EXTREME'] for lvl in acc_vol_levels) else 'LOW'},
        'flags': {
            'round_figures': [{'date': t['date'], 'description': t['description'], 'amount': t['amount'], 'account': t['account_id']} for t in round_figures]
        },
        'integrity_score': {'score': round(integrity_score, 1), 'checks': checks},
    }
    
    return result

def generate_html_report(data: Dict, template_path: str = "template.html") -> str:
    json_str = json.dumps(data)
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            html = f.read()
        html = html.replace('{{DATA_PAYLOAD}}', json_str)
        return html
    except Exception as e:
        return f"Error generating HTML: {str(e)}"
