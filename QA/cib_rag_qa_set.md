# CIB RAG Manual QA Set (From `./cib/` PDFs)

Generated: 2026-03-04 (Africa/Cairo)

This file is meant for fast copy/paste manual testing of the RAG system. Each QA includes:
- A question
- An expected answer grounded in the PDF text
- A source reference (document + page)

Difficulty levels:
- **Simple**: single, direct fact lookup
- **Moderate**: multi-field / compare / list within one document
- **Hard**: multi-step reasoning or cross-document synthesis

## Documents Covered

- `alternative-channelsen.pdf`
- `CIB-Account-EN.pdf`
- `CIB-Customer Service-EN.pdf`
- `CIB-Loans-EN.pdf`
- `CIB-Remittance-EN.pdf`
- `CIB-Teller EN.pdf`
- `Cheques-EN.pdf`
- `Custody.pdf`
- `Debit and Prepaid Fees and Charges EN.pdf`
- `Mortgage.pdf`
- `New International Limits for Debit and Credit Cards - E.pdf`
- `Smart Wallet.pdf`
- `Trade Bills EN.pdf`

---

## `CIB-Loans-EN.pdf`

**Simple**

**S-LOAN-01**  
Q: What is the Assessment Fee for personal loans?  
A: `EGP 200` (paid once).  
Source: `CIB-Loans-EN.pdf` p.1

**S-LOAN-02**  
Q: What is the monthly Loan Service Fee for the **Plus** segment?  
A: `EGP 120` per month.  
Source: `CIB-Loans-EN.pdf` p.1

**S-LOAN-03**  
Q: For **secured** personal loans (up to 8 years), what is the Administration Fee for the **Plus** segment?  
A: `1.75%` of the total loan amount.  
Source: `CIB-Loans-EN.pdf` p.1

**S-LOAN-04**  
Q: What is the fee for issuing a Clearance Letter (loans)?  
A: `EGP 200`.  
Source: `CIB-Loans-EN.pdf` p.1

**S-LOAN-05**  
Q: What is the Liability Letter Issuance Fee?  
A: `EGP 50`.  
Source: `CIB-Loans-EN.pdf` p.1

**Moderate**

**M-LOAN-01**  
Q: What are the early settlement fees for personal loans, and what is the fee if the loan is settled through buy-out?  
A: Partial/full early settlement: `7%` from the total paid amount. Buy-out settlement: `10%` from the total paid amount.  
Source: `CIB-Loans-EN.pdf` p.1

**M-LOAN-02**  
Q: For administration fees (up to 8 years), what are the **Plus** segment rates for secured vs unsecured personal loans?  
A: Secured: `1.75%`. Unsecured: `2.00%`.  
Source: `CIB-Loans-EN.pdf` p.1

**M-LOAN-03**  
Q: What are the Rescheduling Fees and Late Payment Fees (as described in the Loans & Overdrafts fees document)?  
A: Rescheduling: `2%` on the remaining balance. Late payment: `5%` is added to the applied interest rate and applied to the overdue amount.  
Source: `CIB-Loans-EN.pdf` p.1

**M-LOAN-04**  
Q: What is the Government Stamp Duty (paid quarterly) according to the Loans & Overdrafts fees document?  
A: `0.05%` on the highest closing debit balance in the quarter.  
Source: `CIB-Loans-EN.pdf` p.1

**Hard**

**H-LOAN-01**  
Q: Compare the **monthly Loan Service Fee** across segments (Prime, Plus, Wealth, Private).  
A: Prime: `EGP 75`. Plus: `EGP 120`. Wealth: `EGP 200`. Private: `EGP 200`.  
Source: `CIB-Loans-EN.pdf` p.1

**H-LOAN-02**  
Q: For overdrafts, what are the "highest monthly debit balance" fees for secured overdrafts vs revolving unsecured overdrafts?  
A: Secured overdrafts: `0.1%` of the highest closing debit balance in the month (for specified secured categories). Revolving unsecured overdraft: `0.15%` of the highest closing debit balance in the month.  
Source: `CIB-Loans-EN.pdf` p.1

**H-LOAN-03**  
Q: For **secured** personal loans (up to 8 years), compare Administration Fees across Prime vs Plus vs Wealth vs Private.  
A: Prime: `2.00%`. Plus: `1.75%`. Wealth: `1.50%`. Private: `1.25%`.  
Source: `CIB-Loans-EN.pdf` p.1

---

## `Mortgage.pdf`

**Simple**

**S-MORT-01**  
Q: What is the mortgage Administrative Fee?  
A: `2%` of the loan amount (paid once in advance).  
Source: `Mortgage.pdf` p.1

**S-MORT-02**  
Q: What are the Late penalty fees for mortgages?  
A: `5%` over the applied interest rate on the late amounts.  
Source: `Mortgage.pdf` p.1

**S-MORT-03**  
Q: What are the buyout fees for mortgages?  
A: `10%` of the outstanding balance.  
Source: `Mortgage.pdf` p.1

**S-MORT-04**  
Q: What are the partial early settlement fees (mortgage)?  
A: `7%` of the paid amount.  
Source: `Mortgage.pdf` p.1

**S-MORT-05**  
Q: What are the full early settlement fees (mortgage)?  
A: `7%` of the outstanding balance.  
Source: `Mortgage.pdf` p.1

**Moderate**

**M-MORT-01**  
Q: What is the difference between partial early settlement fees and full early settlement fees (mortgage)?  
A: Partial early settlement: `7%` of the paid amount. Full early settlement: `7%` of the outstanding balance.  
Source: `Mortgage.pdf` p.1

**M-MORT-02**  
Q: What are the reschedule fees (mortgage)?  
A: `2%` of the outstanding amount.  
Source: `Mortgage.pdf` p.1

**M-MORT-03**  
Q: Are registration and mortgage fees & expenses fixed?  
A: No, they are "calculated on a case-by-case basis."  
Source: `Mortgage.pdf` p.1

**M-MORT-04**  
Q: List the mortgage fee types mentioned in the document (high level).  
A: Administrative fee (2% of loan amount), partial early settlement (7% of paid amount), full early settlement (7% of outstanding), buyout (10% of outstanding), registration/mortgage fees & expenses (case-by-case), late penalty (5% over applied interest rate), reschedule (2% of outstanding).  
Source: `Mortgage.pdf` p.1

**Hard**

**H-MORT-01**  
Q: If someone partially settles `EGP 100,000` of a mortgage, what is the partial early settlement fee amount?  
A: `7%` of the paid amount, so `EGP 7,000`.  
Source: `Mortgage.pdf` p.1

**H-MORT-02**  
Q: If someone does a full early settlement and the outstanding balance is `EGP 200,000`, what is the full settlement fee?  
A: `7%` of the outstanding balance = `EGP 14,000`.  
Source: `Mortgage.pdf` p.1

**H-MORT-03**  
Q: If the outstanding mortgage balance is `EGP 120,000`, what is the buyout fee amount?  
A: Buyout is `10%` of the outstanding balance = `EGP 12,000`.  
Source: `Mortgage.pdf` p.1

---

## `CIB-Account-EN.pdf`

**Simple**

**S-ACC-01**  
Q: For the **Plus** segment, what is the Account Opening Fee for "EGP Everyday Savers/Savers Account, Classic Current Account"?  
A: `EGP 100` (or its equivalent in foreign currency).  
Source: `CIB-Account-EN.pdf` p.1

**S-ACC-02**  
Q: For the **Plus** segment, what are the Administrative Fees for "EGP Everyday Savers/Savers Account, Classic Current Account"?  
A: `EGP 120/Quarter`.  
Source: `CIB-Account-EN.pdf` p.1

**S-ACC-03**  
Q: For the **Plus** segment, what is the Minimum Balance requirement (Minimum Balance/Threshold Fees section)?  
A: Minimum balance: `EGP 20,000`.  
Source: `CIB-Account-EN.pdf` p.1

**S-ACC-04**  
Q: For the **Plus** segment, what is the "Minimum Balance/Threshold Fees" amount?  
A: `EGP 100`.  
Source: `CIB-Account-EN.pdf` p.1

**S-ACC-05**  
Q: For the "myCIB Account (limited to EGP Everyday savers)", what is the subscription fee?  
A: `EGP 50/Month`.  
Source: `CIB-Account-EN.pdf` p.1

**Moderate**

**M-ACC-01**  
Q: List the **Plus** segment statement fees (deduction quarterly): quarterly, monthly, weekly, daily.  
A: Quarterly: `EGP 100`. Monthly: `EGP 360`. Weekly: `EGP 1,560`. Daily: `EGP 5,000`.  
Source: `CIB-Account-EN.pdf` p.1

**M-ACC-02**  
Q: For the **Plus** segment, what are the E-statement fee and Hold Mail (Annual) fee?  
A: E-statement: `Free`. Hold Mail (Annual): `EGP 3,000`.  
Source: `CIB-Account-EN.pdf` p.1

**M-ACC-03**  
Q: Compare the Mileseverywhere Account (Annual) administrative fee for Plus vs Exclusive Wealth vs Private.  
A: Plus: `EGP 200`. Exclusive Wealth: `Free`. Private: `Free`.  
Source: `CIB-Account-EN.pdf` p.1

**M-ACC-04**  
Q: In Bedaya Accounts, what are the Account Opening Fees and Minimum Balance Fees?  
A: Account opening fees: `Free`. Minimum balance fees: `Free`.  
Source: `CIB-Account-EN.pdf` p.1

**Hard**

**H-ACC-01**  
Q: For "EGP WellSavers Account (Offered only to Wealth/Exclusive Wealth segments)", which segments show an account opening fee of `EGP 1000`?  
A: Wealth, Exclusive Wealth, and Private. (Prime and Plus show `N/A`.)  
Source: `CIB-Account-EN.pdf` p.1

**H-ACC-02**  
Q: In Bedaya Accounts, what are the subscription fees for "Bedaya Saving EGP" and "Bedaya Current USD"?  
A: `EGP 10/Month` for both; the USD current account is `EGP 10/Month` or equivalent in USD.  
Source: `CIB-Account-EN.pdf` p.1

**H-ACC-03**  
Q: Compare the Administrative Fees (per quarter) for "EGP Everyday Savers/Savers Account, Classic Current Account" for Plus vs Exclusive Wealth.  
A: Plus: `EGP 120/Quarter`. Exclusive Wealth: `EGP 50/Quarter`.  
Source: `CIB-Account-EN.pdf` p.1

---

## `CIB-Customer Service-EN.pdf`

**Simple**

**S-CS-01**  
Q: What is the **Plus** segment fee for "Power of Attorney"?  
A: `EGP 100`.  
Source: `CIB-Customer Service-EN.pdf` p.1

**S-CS-02**  
Q: For "Standing Instruction for Internal transfer", what is the **Plus** segment per-transaction fee (excluding subscription)?  
A: `EGP 10 per transaction`.  
Source: `CIB-Customer Service-EN.pdf` p.1

**S-CS-03**  
Q: What are the International Delivery Shipment Fees?  
A: `USD 30`.  
Source: `CIB-Customer Service-EN.pdf` p.1

**S-CS-04**  
Q: What is the "Commission" fee shown in the Customer Service Fees & Charges table?  
A: `EGP 25`.  
Source: `CIB-Customer Service-EN.pdf` p.1

**S-CS-05**  
Q: What is the subscription tariff for standing instructions (internal/external transfer)?  
A: `EGP 50` subscription.  
Source: `CIB-Customer Service-EN.pdf` p.1

**Moderate**

**M-CS-01**  
Q: Compare Prime vs Plus for "Standing Instruction for External transfer" per transaction, and mention the subscription.  
A: Subscription: `EGP 50`. Prime: `EGP 40 per transaction`. Plus: `EGP 10 per transaction`.  
Source: `CIB-Customer Service-EN.pdf` p.1

**M-CS-02**  
Q: What is the fee for "Copy of Document (Per Paper)" for Plus vs Private (include the max caps)?  
A: Plus: `EGP 20 per paper` (Max `EGP 1000`). Private: `EGP 5 per paper` (Max `EGP 250`).  
Source: `CIB-Customer Service-EN.pdf` p.1

**M-CS-03**  
Q: What is the fee for "Distribution of inheritance"?  
A: `0.3%` with a minimum of `EGP 50` and a maximum of `EGP 1,000`.  
Source: `CIB-Customer Service-EN.pdf` p.1

**M-CS-04**  
Q: Compare "Copy of Document (Per Paper)" fees for Prime vs Wealth (include max caps).  
A: Prime: `EGP 20 per paper` (Max `EGP 1000`). Wealth: `EGP 10 per paper` (Max `EGP 500`).  
Source: `CIB-Customer Service-EN.pdf` p.1

**Hard**

**H-CS-01**  
Q: A Plus customer sets up a standing instruction for internal transfer and executes 3 transactions. What are the subscription and per-transaction fees, and the total of those fees?  
A: Subscription: `EGP 50`. Per transaction: `EGP 10`. Total = `EGP 50 + (3 * EGP 10) = EGP 80`.  
Source: `CIB-Customer Service-EN.pdf` p.1

**H-CS-02**  
Q: Which segments have "Power of Attorney" listed as Free?  
A: Exclusive Wealth and Private are `Free`. (Prime/Plus/Wealth show `EGP 100`.)  
Source: `CIB-Customer Service-EN.pdf` p.1

**H-CS-03**  
Q: If a Plus customer requests 30 pages as "Copy of Document (Per Paper)", what fee would apply based on the stated per-paper fee and cap?  
A: `EGP 20 per paper` x 30 = `EGP 600`, which is below the max cap (`EGP 1000`), so `EGP 600`.  
Source: `CIB-Customer Service-EN.pdf` p.1

---

## `CIB-Teller EN.pdf`

**Simple**

**S-TELL-01**  
Q: For the **Plus** segment, what is the fee for "Cash Withdrawal/Deposit over the counter" when cash withdrawal is less than `EGP 30,000` and cash deposit is less than `EGP 20,000`?  
A: `EGP 40`.  
Source: `CIB-Teller EN.pdf` p.1

**S-TELL-02**  
Q: For the **Plus** segment, what is the fee for "Credit Card payment over the counter" when it's less than `EGP 20,000`?  
A: `EGP 40`.  
Source: `CIB-Teller EN.pdf` p.1

**S-TELL-03**  
Q: What is the "Cost per Bag (EGP 70,000)" for cash deposits outside the bank's premises?  
A: `EGP 14`.  
Source: `CIB-Teller EN.pdf` p.1

**S-TELL-04**  
Q: What is the fee for "Cashing under written instructions or Blank Cheques"?  
A: `EGP 10`.  
Source: `CIB-Teller EN.pdf` p.1

**S-TELL-05**  
Q: For "Payment of bank cheques issued by Al Fardan Company in the United Arab Emirates", what is the USD fee shown?  
A: `USD 5`.  
Source: `CIB-Teller EN.pdf` p.1

**Moderate**

**M-TELL-01**  
Q: What is the fee for "Cash deposit with same day value date" (upon customer request)?  
A: `0.2%` with minimum `EGP 100` (or equivalent) and with no maximum.  
Source: `CIB-Teller EN.pdf` p.1

**M-TELL-02**  
Q: Compare the fee rates for cash deposit with same-day value date vs (T+3 customers) vs (T+5 customers).  
A: Same day: `0.2%`. T+3: `0.3%`. T+5: `0.5%`. (All with minimum `EGP 100` or equivalent and no maximum.)  
Source: `CIB-Teller EN.pdf` p.1

**M-TELL-03**  
Q: What is the fee for selling traveler cheques in foreign currency?  
A: `1%` (with Min `USD 2` and no Max).  
Source: `CIB-Teller EN.pdf` p.1

**M-TELL-04**  
Q: For "Payment of bank cheques issued by Al-Jazeera Exchange Qatar", what is the USD fee shown?  
A: `USD 3`.  
Source: `CIB-Teller EN.pdf` p.1

**Hard**

**H-TELL-01**  
Q: Which segments are listed as Free for "Cash Withdrawal/Deposit over the counter" under the thresholds (cash withdrawal < `EGP 30,000`, cash deposit < `EGP 20,000`)?  
A: Wealth, Exclusive Wealth, and Private are `Free`. Prime and Plus are `EGP 40`.  
Source: `CIB-Teller EN.pdf` p.1

**H-TELL-02**  
Q: If a customer deposits `EGP 30,000` with same-day value date, what fee would be applied based on the stated rules?  
A: `0.2%` of `EGP 30,000` is `EGP 60`, but the minimum is `EGP 100`, so the fee would be `EGP 100`.  
Source: `CIB-Teller EN.pdf` p.1

**H-TELL-03**  
Q: List the USD fees shown for cashing/payment of bank cheques issued by these entities: Al Fardan (UAE), Al Rajhi (Saudi Arabia), Al-Jazeera (Qatar), Al Ansari, Thomas Cook Al Rostamani, Bahrain Exchange (Kuwait).  
A: Al Fardan (USD): `USD 5`. Al Rajhi (USD): `USD 2`. Al-Jazeera (USD): `USD 3`. Al Ansari (USD): `USD 2`. Thomas Cook Al Rostamani (USD): `USD 2`. Bahrain Exchange (USD): `USD 2`.  
Source: `CIB-Teller EN.pdf` p.1

---

## `alternative-channelsen.pdf`

**Simple**

**S-ALT-01**  
Q: What is the fee for "Hard Token OTP"?  
A: `EGP 225`.  
Source: `alternative-channelsen.pdf` p.1

**S-ALT-02**  
Q: What is the fee for "Transfer - Outward Local transfers in EGP" via online banking (for individual customers)?  
A: `No Fees for individual customers`.  
Source: `alternative-channelsen.pdf` p.1

**S-ALT-03**  
Q: What is the fee for "Balance Inquiries and Mini Statements" via IPN through service provider apps (Instapay)?  
A: `EGP 0.50`.  
Source: `alternative-channelsen.pdf` p.1

**S-ALT-04**  
Q: What is the fee for "One-Time Password (OTP)"?  
A: `No Fees`.  
Source: `alternative-channelsen.pdf` p.1

**S-ALT-05**  
Q: What is the fee for "Meeza Wallets transfers through Internet or Mobile Banking"?  
A: `No Fees`.  
Source: `alternative-channelsen.pdf` p.1

**Moderate**

**M-ALT-01**  
Q: What is the fee for "Transfer - Outward Through Internet or Mobile Banking in FCY"?  
A: `0.3%` (minimum `USD 20`, maximum `USD 150` or its equivalent) `+ swift or telex charges + correspondent charges` based on the transfer currency.  
Source: `alternative-channelsen.pdf` p.1

**M-ALT-02**  
Q: What is the fee for "Money transfer between individuals through Instant Payment Network (IPN)"?  
A: `0.1%` (minimum `EGP 0.5`, maximum `EGP 20`).  
Source: `alternative-channelsen.pdf` p.1

**M-ALT-03**  
Q: What are the checkbook fees when requested through Internet or Mobile Banking?  
A: `EGP 10 / leaf`. Examples shown: `EGP 480` (48 leaves), `EGP 240` (24 leaves), `EGP 120` (12 leaves), or equivalent in USD.  
Source: `alternative-channelsen.pdf` p.1

**M-ALT-04**  
Q: What is the Instapay (IPN through service provider apps) fee for "Money transfer between individuals"?  
A: `0.10%` (minimum `EGP 0.50`, maximum `EGP 20`).  
Source: `alternative-channelsen.pdf` p.1

**Hard**

**H-ALT-01**  
Q: Which services in the Digital Channels document are explicitly listed as "No Fees"?  
A: E-statement (Quarter Deduction): `No Fees`. One-Time Password (OTP): `No Fees`. Outward local transfers in EGP (individual customers): `No Fees`. Meeza Wallets transfers: `No Fees`.  
Source: `alternative-channelsen.pdf` p.1

**H-ALT-02**  
Q: In Instapay (IPN through service provider apps), what is the fee for "Money transfer between individuals", and what is the fee for "Balance Inquiries and Mini Statements"?  
A: Money transfer: `0.10%` (minimum `EGP 0.50`, maximum `EGP 20`). Balance inquiries / mini statements: `EGP 0.50`.  
Source: `alternative-channelsen.pdf` p.1

**H-ALT-03**  
Q: For "Cash withdrawal or balance inquiry with a non-CIB card", what does the table state for non-CIB customers?  
A: It says "Subject to issuing bank's tariff."  
Source: `alternative-channelsen.pdf` p.1

---

## `CIB-Remittance-EN.pdf`

**Simple**

**S-REM-01**  
Q: What is the fee for outgoing transfers "Over the counter in Local Currency swift transfer order"?  
A: `0.2%` (Minimum `EGP 40` - Maximum `EGP 350`) `+ Telex or Swift Charges + 15 EGP Correspondent Charges`.  
Source: `CIB-Remittance-EN.pdf` p.1

**S-REM-02**  
Q: For incoming Swift transfers in foreign currency (SHA/BEN), what is the fee shown?  
A: `USD 4` (or equivalent).  
Source: `CIB-Remittance-EN.pdf` p.1

**S-REM-03**  
Q: What is the fee for outgoing transfers "Through Internet or Mobile Banking in Local Currency through ACH"?  
A: `Free for Individual customers until 15 February 2026`.  
Source: `CIB-Remittance-EN.pdf` p.1

**S-REM-04**  
Q: What is the fee for "Amendment of incoming Transfer data"?  
A: `USD 5 or EGP 30`.  
Source: `CIB-Remittance-EN.pdf` p.1

**S-REM-05**  
Q: What is the fee for "Credit confirmation" (Other Services / inward transfers section)?  
A: `USD 25 OR EGP 50`.  
Source: `CIB-Remittance-EN.pdf` p.1

**Moderate**

**M-REM-01**  
Q: What is the fee for outgoing transfers "Over the counter in Foreign Currency"?  
A: `0.3%` (Minimum `USD 20` - Maximum `USD 150`) or equivalent `+ Telex or Swift Charges + Correspondent Charges`.  
Source: `CIB-Remittance-EN.pdf` p.1

**M-REM-02**  
Q: What is the fee for "Cancellation of Outgoing Transfer" (Investigations Services for Outgoing Transfers)?  
A: `USD 25 OR EGP 80`.  
Source: `CIB-Remittance-EN.pdf` p.1

**M-REM-03**  
Q: What is the fee for "Third Party incoming payments"?  
A: `0.2%` (Minimum `EGP 40` - Maximum `EGP 350`) `+ Telex or Swift Charges + EGP 15 Correspondent Charges`.  
Source: `CIB-Remittance-EN.pdf` p.1

**M-REM-04**  
Q: What is the fee for outgoing transfers "Through Internet or Mobile Banking in Local Currency swift transfer order"?  
A: `0.2%` (Minimum `EGP 40` - Maximum `EGP 350`) `+ Telex or Swift Charges + 15 EGP Correspondent Charges`.  
Source: `CIB-Remittance-EN.pdf` p.1

**Hard**

**H-REM-01**  
Q: Compare ACH vs IPN for outgoing transfers through Internet/Mobile Banking in local currency.  
A: ACH: `Free for Individual customers until 15 February 2026`. IPN: `0.1%` (minimum `EGP 0.5`, maximum `EGP 20`).  
Source: `CIB-Remittance-EN.pdf` p.1

**H-REM-02**  
Q: List the fees for "Investigations Services for Outgoing Transfers" (Amendment details, Beneficiary claims, Cancellation).  
A: Amendment details: `USD 25 OR EGP 50`. Beneficiary claims: `USD 25 OR EGP 50`. Cancellation of outgoing transfer: `USD 25 OR EGP 80`.  
Source: `CIB-Remittance-EN.pdf` p.1

**H-REM-03**  
Q: List the fees shown for Investigations/Other Services related to inward transfers: refund, inquiry from sender bank about invalid/missing data, credit confirmation, replying to a claim implemented since more than 1 year, and amendment of incoming transfer data.  
A: Refund: `USD 25 OR EGP 50`. Inquiry invalid/missing data: `USD 20 or EGP 50`. Credit confirmation: `USD 25 OR EGP 50`. Reply to claim since more than 1 year: `USD 50`. Amendment of incoming transfer data: `USD 5 or EGP 30`.  
Source: `CIB-Remittance-EN.pdf` p.1

---

## `Cheques-EN.pdf`

**Simple**

**S-CHQ-01**  
Q: In the Chequebooks Fees & Charges table, what is the "Stop Payment (Per Cheque)" fee for **Plus** (EGP and USD)?  
A: `EGP 25` and `USD 5` (or its equivalent).  
Source: `Cheques-EN.pdf` p.1

**S-CHQ-02**  
Q: What is the "Draft Cheque issuance" fee (EGP)?  
A: `0.2%` (with Min `EGP 30` and Max `EGP 400`) `+ Swift Charges`.  
Source: `Cheques-EN.pdf` p.1

**S-CHQ-03**  
Q: What is the fee for "Return Cheques drawn on CIB's Customers"?  
A: `EGP 50 / USD 7`.  
Source: `Cheques-EN.pdf` p.4

**S-CHQ-04**  
Q: What is the fee for "Cancellation of a Draft cheque or a Certified cheque for payment (accepted payment) or payment order"?  
A: `EGP 10`.  
Source: `Cheques-EN.pdf` p.1

**S-CHQ-05**  
Q: What is the fee to amend the beneficiary's account number on postdated cheques?  
A: `EGP 5 / USD 0.25`.  
Source: `Cheques-EN.pdf` p.5

**Moderate**

**M-CHQ-01**  
Q: What are the Chequebooks Issuance prices for 48 pages, 24 pages, and 12 pages?  
A: 48 pages: `EGP 480` (or equivalent in USD). 24 pages: `EGP 240` (or equivalent in USD). 12 pages: `EGP 120` (or equivalent in USD).  
Source: `Cheques-EN.pdf` p.1

**M-CHQ-02**  
Q: For "Collection of Cheques favor of CIB's Customers (Normal Collection)", what is the fee for LCY cheques inside CBE clearing house (ATM deposit: waived 50%) for **Plus**?  
A: `EGP 20`. (Exclusive Wealth and Private are shown as Free.)  
Source: `Cheques-EN.pdf` p.2

**M-CHQ-03**  
Q: What are the courier fees inside Egypt for LCY and FCY cheques (as stated in General Notes)?  
A: Inside Egypt courier fees: LCY `EGP 50` (urban areas) / `EGP 100` (remote areas). FCY: `USD 5`.  
Source: `Cheques-EN.pdf` p.5

**M-CHQ-04**  
Q: What is the fee for "Certified Cheque issuance" in USD (as shown)?  
A: `0.3%` (with Min `USD 15` and Max `USD 150` or equivalent) `+ Telex or Swift charges (if any)`.  
Source: `Cheques-EN.pdf` p.1

**M-CHQ-05**  
Q: What is the fee for "Cheques Postponement" (postdated cheques services)?  
A: `EGP 20 / USD 2`.  
Source: `Cheques-EN.pdf` p.4

**Hard**

**H-CHQ-01**  
Q: For "Immediate Credit" customers, what is the fee for **LCY cheques outside CBE clearing house**?  
A: `0.9%` (Min `EGP 35` / Without Max) `+ Correspondent Fees + Courier Fees`.  
Source: `Cheques-EN.pdf` p.3

**H-CHQ-02**  
Q: Compare FCY cheques outside CBE clearing house fees for (1) normal collection vs (2) immediate credit customers.  
A: Normal collection: `0.3%` (Min `USD 10` / Max `USD 100`) `+ Correspondent Fees + Courier Fees`. Immediate credit: `1.2%` (Min `USD 10` / Without Max) `+ Correspondent Fees + Courier Fees`.  
Source: `Cheques-EN.pdf` p.3

**H-CHQ-03**  
Q: For "Normal Collection" (LCY checks outside CBE clearing house), what fee formula is shown?  
A: `0.2%` Min `EGP 20` / Max `EGP 400` `+ Correspondent Fees + Courier Fees`. (Exclusive Wealth/Private show "50% Discount + Correspondent Fees + Courier Fees".)  
Source: `Cheques-EN.pdf` p.2

**H-CHQ-04**  
Q: What courier fees are stated for "Outside Egypt" in the General Notes?  
A: Outside Egypt: `USD 35` within Middle East region / `USD 50` outside Middle East region; and `USD 25` for foreign currency checks collected through a correspondent.  
Source: `Cheques-EN.pdf` p.5

---

## `Smart Wallet.pdf`

**Simple**

**S-SW-01**  
Q: What is the fee for "Cash Withdrawals via ATMs" in Smart Wallet?  
A: `1%` with min. `EGP 3`.  
Source: `Smart Wallet.pdf` p.1

**S-SW-02**  
Q: For "Online Card", what is the fixed issuance fee for a single-use online card transaction?  
A: Fixed `EGP 10` upon issuance `+ Markup fees`.  
Source: `Smart Wallet.pdf` p.1

**S-SW-03**  
Q: In Corporate Disbursement Pricing, what is the Disbursement Fee for "From 1 to 100 transactions"?  
A: `EGP 10`.  
Source: `Smart Wallet.pdf` p.2

**S-SW-04**  
Q: What is the fee for "Withdrawals via Service Provider"?  
A: `1.5%` min. `EGP 3`.  
Source: `Smart Wallet.pdf` p.1

**S-SW-05**  
Q: In Corporate Disbursement Pricing, what are the ATM Withdrawal Fees shown?  
A: `No fees` (for the listed monthly transaction amount tiers).  
Source: `Smart Wallet.pdf` p.2

**Moderate**

**M-SW-01**  
Q: What are the "Send Money" fees (On-us vs Off-us) and the special rule about the first transaction of the month?  
A: On-us: `EGP 1`. Off-us: `0.5%` with max `EGP 15`. First transaction of the month is free for a maximum of `EGP 2,000`.  
Source: `Smart Wallet.pdf` p.1

**M-SW-02**  
Q: Compare Corporate Disbursement fees for "From 101 to 500 transactions" vs "1000+ transactions".  
A: 101-500: `EGP 9`. 1000+: `EGP 7`.  
Source: `Smart Wallet.pdf` p.2

**M-SW-03**  
Q: In Smart Wallet Payroll Disbursement Pricing, what is the Disbursement Fee for "1000+ transactions"?  
A: `EGP 4`.  
Source: `Smart Wallet.pdf` p.2

**M-SW-04**  
Q: For "Online Card", what is the fixed issuance fee for a multi-use online card transaction?  
A: Fixed `EGP 15` upon issuance `+ Markup fees`.  
Source: `Smart Wallet.pdf` p.1

**M-SW-05**  
Q: In Smart Wallet Payroll Disbursement Pricing, what is the Disbursement Fee for "From 1 to 100 transactions"?  
A: `EGP 7`.  
Source: `Smart Wallet.pdf` p.2

**Hard**

**H-SW-01**  
Q: For 501-1000 transactions, what is the difference between Corporate vs Payroll Disbursement Fees?  
A: Corporate: `EGP 8`. Payroll: `EGP 5`. Difference = `EGP 3`.  
Source: `Smart Wallet.pdf` p.2

**H-SW-02**  
Q: In Smart Wallet, which services are explicitly listed as "Free"?  
A: Registration Fees, Annual Fees, Cash Deposit via ATMs, Cash Deposit via Service Provider, Mobile Recharging, Purchasing via Merchants (all shown as `Free`).  
Source: `Smart Wallet.pdf` p.1

**H-SW-03**  
Q: For "Send Money" Off-us, the fee is 0.5% with a max of EGP 15. If someone sends `EGP 10,000` Off-us, what fee would apply?  
A: `0.5%` of `EGP 10,000` is `EGP 50`, but the fee is capped at `EGP 15`, so `EGP 15`.  
Source: `Smart Wallet.pdf` p.1

**H-SW-04**  
Q: For 1-100 transactions, compare Corporate vs Payroll Disbursement Fees and compute the difference.  
A: Corporate: `EGP 10`. Payroll: `EGP 7`. Difference = `EGP 3`.  
Source: `Smart Wallet.pdf` p.2

---

## `Trade Bills EN.pdf`

**Simple**

**S-TB-01**  
Q: For the Plus column, what is the fee structure for "Collection in Favor of CIB Customers (Whether Drawn on CIB or Another Bank)"?  
A: `0.2% Equally` (Min `EGP 10/USD 2` - Without Max.) `+ EGP 10/USD 2 Custodian Fees + EGP 40/USD 5 Protest Fees + Correspondent Fees + Courier Fees`.  
Source: `Trade Bills EN.pdf` p.1

**S-TB-02**  
Q: What is the base fee for "Postpone Bills for Collection" if the bill hasn't been sent for collection?  
A: `EGP 10/USD 2`.  
Source: `Trade Bills EN.pdf` p.1

**S-TB-03**  
Q: What is the base fee for "Withdraw Bills for Collection" if the bill hasn't been sent for collection?  
A: `0.1%` Min `EGP 15 / USD 3`.  
Source: `Trade Bills EN.pdf` p.1

**S-TB-04**  
Q: What is the fee for "Postpone Bills for Collection" if the bill has been sent for collection?  
A: `EGP 10/USD 2 + Correspondent Fees + Courier Fees`.  
Source: `Trade Bills EN.pdf` p.1

**S-TB-05**  
Q: What is the base fee for "Postpone Bills for Guarantee" if the bill hasn't been sent for collection?  
A: `EGP 30/USD 6`.  
Source: `Trade Bills EN.pdf` p.1

**Moderate**

**M-TB-01**  
Q: For "Postpone Bills for Guarantee", what is the fee if the bill has been sent for collection?  
A: `EGP 30/USD 6 + Correspondent Fees + Courier Fees`.  
Source: `Trade Bills EN.pdf` p.1

**M-TB-02**  
Q: For "Postpone Bills for Guarantee", what is the fee if the bill hasn't been sent for collection?  
A: `EGP 30/USD 6`.  
Source: `Trade Bills EN.pdf` p.1

**M-TB-03**  
Q: For "Withdraw Bills for Collection", what is the fee if the bill was sent for collection?  
A: `0.1%` Min `EGP 15/USD 3 + Correspondent Fees + Courier Fees`.  
Source: `Trade Bills EN.pdf` p.1

**M-TB-04**  
Q: For "Postpone Bills for Collection", compare the fees when the bill hasn't been sent vs when it has been sent.  
A: Not sent: `EGP 10/USD 2`. Sent: `EGP 10/USD 2 + Correspondent Fees + Courier Fees`.  
Source: `Trade Bills EN.pdf` p.1

**Hard**

**H-TB-01**  
Q: Compare the base fee (not sent for collection) for "Postpone Bills for Collection" vs "Postpone Bills for Guarantee".  
A: Postpone for collection: `EGP 10/USD 2`. Postpone for guarantee: `EGP 30/USD 6`.  
Source: `Trade Bills EN.pdf` p.1

**H-TB-02**  
Q: If the bill was sent for collection and you want to withdraw it, what fees apply?  
A: `0.1%` Min `EGP 15/USD 3` + `Correspondent Fees` + `Courier Fees`.  
Source: `Trade Bills EN.pdf` p.1

**H-TB-03**  
Q: For "Collection in Favor of CIB Customers", list the named fee components in addition to the 0.2% commission.  
A: In addition to `0.2% Equally` (Min `EGP 10/USD 2` - Without Max.), the table lists: `EGP 10/USD 2 Custodian Fees`, `EGP 40/USD 5 Protest Fees`, `Correspondent Fees`, and `Courier Fees`.  
Source: `Trade Bills EN.pdf` p.1

---

## `Custody.pdf`

**Simple**

**S-CUST-01**  
Q: What is the fee to open a custody account for the customer (individual/establishments)?  
A: `EGP 50` in addition to the required stamp duties.  
Source: `Custody.pdf` p.1

**S-CUST-02**  
Q: What is the fee for "Issuing a statement of account" (upon request)?  
A: `EGP 50` upon request.  
Source: `Custody.pdf` p.1

**S-CUST-03**  
Q: What is the fee for "Modifying the customer's name at the stock exchange"?  
A: `EGP 100`.  
Source: `Custody.pdf` p.1

**S-CUST-04**  
Q: What is the fee for "Issuing a certificate"?  
A: `EGP 50` upon request.  
Source: `Custody.pdf` p.1

**S-CUST-05**  
Q: What is the fee for "Reactivating custody account"?  
A: `EGP 50` for each account.  
Source: `Custody.pdf` p.1

**Moderate**

**M-CUST-01**  
Q: What is the fee for "Depositing securities with the central Custody"?  
A: `0.05%` of the previous day closing price for each security type; minimum `EGP 50` and maximum `EGP 5000`.  
Source: `Custody.pdf` p.1

**M-CUST-02**  
Q: What is the annual custody charging fee for securities deposited with the central custody?  
A: `0.1%` annual deduction on 31/12 collected on each security; minimum `EGP 50`.  
Source: `Custody.pdf` p.1

**M-CUST-03**  
Q: What is the holding fee for securities deposited with external (foreign) correspondents?  
A: `0.125%` per annum collected every six months; minimum `USD 25` semiannually.  
Source: `Custody.pdf` p.1

**M-CUST-04**  
Q: What is the fee for "Collecting cash dividends"?  
A: `1%` of the net value of the collected coupon amount after deducting taxes; minimum `EGP 10` and maximum `EGP 1000`.  
Source: `Custody.pdf` p.1

**M-CUST-05**  
Q: What is the annual safekeeping fee for Egyptian treasury bills and bonds?  
A: `0.0001` from the par value of balance on December 31 of every year; minimum `EGP 50`.  
Source: `Custody.pdf` p.1

**Hard**

**H-CUST-01**  
Q: What are the fee tiers for "Receiving subscription / buying and selling securities deposited with the central custody"?  
A: Up to `EGP 5 Million` (or equivalent): `0.1%` (minimum `EGP 50`) with `No maximum`. More than `EGP 5 Million`: `0.05%`.  
Source: `Custody.pdf` p.1

**H-CUST-02**  
Q: What is the fee for "Settling Treasury Bills / Bonds in the primary and secondary market"?  
A: `0.0005` calculated on the par value with minimum `EGP 250` and maximum `EGP 2,000` per tier.  
Source: `Custody.pdf` p.1

**H-CUST-03**  
Q: What is the fee for "Global Depository Receipt (GDR)"?  
A: `USD 60` per transaction for issuance or cancellation, in addition to commissions collected from the foreign depository bank and any other third-party fees paid as part of settlement.  
Source: `Custody.pdf` p.1

---

## `Debit and Prepaid Fees and Charges EN.pdf`

**Simple**

**S-CARD-01**  
Q: For the "Plus Titanium" debit card, what are the issuance fees and annual fees?  
A: Issuance: `EGP 75`. Annual: `EGP 75`.  
Source: `Debit and Prepaid Fees and Charges EN.pdf` p.1

**S-CARD-02**  
Q: For the "Plus Titanium" debit card, what is the cash withdrawal fee from other domestic ATMs or POSs?  
A: `EGP 5`.  
Source: `Debit and Prepaid Fees and Charges EN.pdf` p.1

**S-CARD-03**  
Q: For the "Plus Titanium" debit card, what is the supplementary card annual fee?  
A: `EGP 200`.  
Source: `Debit and Prepaid Fees and Charges EN.pdf` p.1

**S-CARD-04**  
Q: For the "Plus Titanium" debit card, what is the cash withdrawal fee from CIB ATMs?  
A: `Free`.  
Source: `Debit and Prepaid Fees and Charges EN.pdf` p.1

**S-CARD-05**  
Q: For the "Plus Titanium" debit card, what is the daily local cash withdrawal limit?  
A: `EGP 30,000`.  
Source: `Debit and Prepaid Fees and Charges EN.pdf` p.1

**Moderate**

**M-CARD-01**  
Q: What is the daily local purchase limit for the "Plus Titanium" debit card?  
A: `EGP 500,000`.  
Source: `Debit and Prepaid Fees and Charges EN.pdf` p.1

**M-CARD-02**  
Q: Compare annual fees for "Wealth Platinum" debit card vs "Private" debit card.  
A: Wealth Platinum annual fees: `EGP 75`. Private annual fees: `Free`.  
Source: `Debit and Prepaid Fees and Charges EN.pdf` p.1

**M-CARD-03**  
Q: List the prepaid cards issuance fees for: Charge & Go, ISIC, Meeza Karty, Meeza Governmental, MasterCard Payroll.  
A: Charge & Go: `EGP 150`. ISIC: `Free`. Meeza Karty: `EGP 50`. Meeza Governmental: `Free`. MasterCard Payroll: `EGP 100`.  
Source: `Debit and Prepaid Fees and Charges EN.pdf` p.1

**M-CARD-04**  
Q: What is the daily local online purchase limit for the "Plus Titanium" debit card?  
A: `EGP 150,000`.  
Source: `Debit and Prepaid Fees and Charges EN.pdf` p.1

**M-CARD-05**  
Q: Compare the daily local purchase limit for "Wealth Platinum" debit card vs "Private" debit card.  
A: Wealth Platinum: `EGP 1,000,000`. Private: `EGP 2,500,000`.  
Source: `Debit and Prepaid Fees and Charges EN.pdf` p.1

**Hard**

**H-CARD-01**  
Q: For debit cards linked to foreign currency accounts, what is the cash withdrawal fee from international ATMs?  
A: `4%` with min. `EGP 20`.  
Source: `Debit and Prepaid Fees and Charges EN.pdf` p.1

**H-CARD-02**  
Q: In the prepaid cards table, what is the replacement fee for "Meeza Governmental", and what is the cash withdrawal fee from other domestic ATMs/POSs?  
A: Replacement Fees (Meeza Governmental): `EGP 20`. Cash withdrawal from other domestic ATMs/POSs: `EGP 5`.  
Source: `Debit and Prepaid Fees and Charges EN.pdf` p.1

**H-CARD-03**  
Q: In the prepaid cards table, what is the cash withdrawal fee from international ATMs for the ISIC card (as shown)?  
A: `10%` with a minimum of `20 EGP`.  
Source: `Debit and Prepaid Fees and Charges EN.pdf` p.1

**H-CARD-04**  
Q: In the prepaid cards table, what foreign exchange markup fee is shown for the ISIC card?  
A: `5%`.  
Source: `Debit and Prepaid Fees and Charges EN.pdf` p.1

---

## `New International Limits for Debit and Credit Cards - E.pdf`

**Simple**

**S-LIM-01**  
Q: For the **Plus segment**, what is the monthly international cash withdrawal limit (Outside Egypt)?  
A: `EGP 5,000`.  
Source: `New International Limits for Debit and Credit Cards - E.pdf` p.1

**S-LIM-02**  
Q: For the **Plus segment**, what is the monthly international purchase limit (Outside Egypt)?  
A: `EGP 400,000`.  
Source: `New International Limits for Debit and Credit Cards - E.pdf` p.1

**S-LIM-03**  
Q: For the **Plus segment**, what is the monthly international purchase limit (Inside Egypt)?  
A: `EGP 150,000`.  
Source: `New International Limits for Debit and Credit Cards - E.pdf` p.1

**S-LIM-04**  
Q: What email address is provided for activating the international purchase limit?  
A: `International.cardtravel@cibeg.com`.  
Source: `New International Limits for Debit and Credit Cards - E.pdf` p.1

**S-LIM-05**  
Q: How many days before travel does the document recommend informing the bank?  
A: `3 to 7 days` prior to your travel date.  
Source: `New International Limits for Debit and Credit Cards - E.pdf` p.1

**Moderate**

**M-LIM-01**  
Q: Name two ways to activate your credit card's international purchase limit (as described).  
A: Examples: email `International.cardtravel@cibeg.com` (with required identifiers), or visit a CIB branch / call center, or contact your relationship manager.  
Source: `New International Limits for Debit and Credit Cards - E.pdf` p.1

**M-LIM-02**  
Q: How long is the approved international purchase limit valid for (after request)?  
A: `30 days` from the date of your request.  
Source: `New International Limits for Debit and Credit Cards - E.pdf` p.1

**M-LIM-03**  
Q: If you do not notify the bank that you are traveling abroad, what happens to your monthly international purchase limit?  
A: It remains the same as the "Inside Egypt" limits table based on your segment type.  
Source: `New International Limits for Debit and Credit Cards - E.pdf` p.1

**M-LIM-04**  
Q: What foreign exchange markup fee does the document state for all cards?  
A: `3%` for all cards.  
Source: `New International Limits for Debit and Credit Cards - E.pdf` p.1

**Hard**

**H-LIM-01**  
Q: What documents can the bank request within 90 days from activation (per the note)?  
A: The document lists items such as: copy of passport, departure stamp from Egypt on passport during the activation period, registered mobile number, last 4 digits of the activated credit card international limit, and ID/passport number.  
Source: `New International Limits for Debit and Credit Cards - E.pdf` p.1

**H-LIM-02**  
Q: Compare the Plus segment monthly international purchase limit outside Egypt vs inside Egypt, and compute the difference.  
A: Outside Egypt: `EGP 400,000`. Inside Egypt: `EGP 150,000`. Difference: `EGP 250,000`.  
Source: `New International Limits for Debit and Credit Cards - E.pdf` p.1

**H-LIM-03**  
Q: What restriction is stated for newly issued credit cards regarding international transactions, and from when is it effective?  
A: Effective `April 1st, 2024`, any newly issued credit card is restricted from international transactions for the first `two months` from the issuance date.  
Source: `New International Limits for Debit and Credit Cards - E.pdf` p.1

**H-LIM-04**  
Q: What does the document say about debit cards linked to local currency accounts and international spending?  
A: "International spend on debit cards linked to local currency accounts has been stopped."  
Source: `New International Limits for Debit and Credit Cards - E.pdf` p.1

---

## Cross-Document (Hard, Broad-Scope)

**H-X-01**  
Q: For the Plus segment, what is (1) the monthly loan service fee, and (2) the quarterly administrative fee for Everyday Savers/Savers or Classic Current Account?  
A: (1) Monthly loan service fee: `EGP 120`. (2) Quarterly administrative fee: `EGP 120/Quarter`.  
Source: `CIB-Loans-EN.pdf` p.1; `CIB-Account-EN.pdf` p.1

**H-X-02**  
Q: A Plus customer wants to make an outward transfer in foreign currency via Internet/Mobile Banking. What is the fee rate and the min/max shown?  
A: `0.3%` (minimum `USD 20`, maximum `USD 150` or equivalent) plus `swift/telex` charges and `correspondent` charges.  
Source: `alternative-channelsen.pdf` p.1; `CIB-Remittance-EN.pdf` p.1

**H-X-03**  
Q: Compare Plus vs Wealth for (1) account opening fee (Everyday Savers/Savers), and (2) teller cash withdrawal under the stated thresholds.  
A: Account opening: Plus `EGP 100`, Wealth `Free`. Teller cash withdrawal under threshold: Plus `EGP 40`, Wealth `Free`.  
Source: `CIB-Account-EN.pdf` p.1; `CIB-Teller EN.pdf` p.1

**H-X-04**  
Q: A Plus customer wants to (1) withdraw cash over the counter for less than `EGP 30,000`, and (2) withdraw cash via Smart Wallet ATMs. What fees apply in each case?  
A: Over the counter (Plus): `EGP 40`. Smart Wallet ATM withdrawal: `1%` with min `EGP 3`.  
Source: `CIB-Teller EN.pdf` p.1; `Smart Wallet.pdf` p.1

**H-X-05**  
Q: Online checkbook fees show `EGP 10/leaf`, and Chequebooks issuance shows `EGP 480` for 48 pages. Do these values align numerically?  
A: Yes. `48 * EGP 10 = EGP 480`, matching the 48-pages chequebook issuance price shown.  
Source: `alternative-channelsen.pdf` p.1; `Cheques-EN.pdf` p.1

**H-X-06**  
Q: Compare the Plus segment **credit card** monthly international purchase limit (Outside Egypt) vs the "Plus Titanium" **debit card** daily local purchase limit.  
A: Credit card (Plus segment, outside Egypt): `EGP 400,000` per month. Debit card (Plus Titanium): `EGP 500,000` per day (local purchase limit).  
Source: `New International Limits for Debit and Credit Cards - E.pdf` p.1; `Debit and Prepaid Fees and Charges EN.pdf` p.1

**H-X-07**  
Q: What is the fee for "Stop Payment (Per Cheque)" in EGP, and what is the fee for "Cancellation of Outgoing Transfer"?  
A: Stop payment per cheque: `EGP 25`. Cancellation of outgoing transfer: `USD 25 OR EGP 80`.  
Source: `Cheques-EN.pdf` p.1; `CIB-Remittance-EN.pdf` p.1

**H-X-08**  
Q: Compare two "small fees": (1) cash withdrawal from other domestic ATMs/POSs for the Plus Titanium debit card, and (2) balance inquiries/mini statements via Instapay (IPN apps).  
A: Debit card other domestic ATMs/POSs: `EGP 5`. Instapay balance inquiries/mini statements: `EGP 0.50`.  
Source: `Debit and Prepaid Fees and Charges EN.pdf` p.1; `alternative-channelsen.pdf` p.1

**H-X-09**  
Q: Compare the custody fee for "Issuing a statement of account" vs the Plus fee for "Copy of Document (Per Paper)".  
A: Custody statement of account: `EGP 50` upon request. Copy of document (Plus): `EGP 20 per paper` (Max `EGP 1000`).  
Source: `Custody.pdf` p.1; `CIB-Customer Service-EN.pdf` p.1

**H-X-10**  
Q: A Plus customer wants (1) to cash under written instructions/blank cheques at a branch, and (2) to request a chequebook online. What are the fees shown for each?  
A: Cashing under written instructions/blank cheques: `EGP 10`. Online chequebook request: `EGP 10 / leaf` (examples: EGP 480 for 48 leaves, EGP 240 for 24, EGP 120 for 12).  
Source: `CIB-Teller EN.pdf` p.1; `alternative-channelsen.pdf` p.1
