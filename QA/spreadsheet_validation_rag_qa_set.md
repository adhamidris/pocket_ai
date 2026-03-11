# Spreadsheet Validation Manual QA Set

Generated: 2026-03-10 (Africa/Cairo)

This file is for manual RAG testing against spreadsheet and CSV sources in `/documents/spreadsheet_validation/` and the California invoice workbooks in `/documents/business/invoicing/`.

## Documents Covered

- `california_energy_standard_invoice_template_blank.xlsx`
- `california_energy_standard_invoice_template_example.xlsx`
- `applied-jobs-april24.xlsx`
- `jobs_sheet.csv`
- `purchasing-sales-data.csv`

---

## `california_energy_standard_invoice_template_blank.xlsx`

**Simple**

**S-CABL-01**  
Q: What template version appears in the blank California energy invoice workbook?  
A: `09/23/25`.  
Source: `california_energy_standard_invoice_template_blank.xlsx` sheet `Instructions`

**S-CABL-02**  
Q: Which cells are users told to enter data into?  
A: Cells highlighted in `Blue`.  
Source: `california_energy_standard_invoice_template_blank.xlsx` sheet `Instructions`

**Moderate**

**M-CABL-01**  
Q: What does the workbook say about non-blue cells?  
A: Cells not highlighted in blue are `locked from editing`.  
Source: `california_energy_standard_invoice_template_blank.xlsx` sheet `Instructions`

**M-CABL-02**  
Q: What confidentiality warning does the workbook give?  
A: It says to `avoid disclosing trade secrets and confidential information` because the documents are publicly accessible.  
Source: `california_energy_standard_invoice_template_blank.xlsx` sheet `Instructions`

**M-CABL-03**  
Q: Which budget category tabs are specially colored to indicate line-item details can be entered?  
A: `Equipment` and `Subrecipients & Vendors`, shown as `orange` tabs.  
Source: `california_energy_standard_invoice_template_blank.xlsx` sheet `Instructions`

**Hard**

**H-CABL-01**  
Q: What rounding rules are given for currency rates, percentage rates, and quantity values?  
A: Currency rates should round to the `cent ($0.01)`, percentage rates to a `maximum of two decimal places`, and quantity values to a `maximum of two decimal places`.  
Source: `california_energy_standard_invoice_template_blank.xlsx` sheet `Instructions`

**H-CABL-02**  
Q: What does the workbook say about entered/totals budget values versus calculated currency values?  
A: Entered and totaled CEC and Match share budget values should round to the `dollar ($1)`, and calculated currency values should also round to the `dollar ($1)`.  
Source: `california_energy_standard_invoice_template_blank.xlsx` sheet `Instructions`

**H-CABL-03**  
Q: What does the workbook say the sandbox area can be used for?  
A: It can be used for `rough work`, including `notes`, `calculations`, and `derivations`, associated with the print area.  
Source: `california_energy_standard_invoice_template_blank.xlsx` sheet `Instructions`

---

## `california_energy_standard_invoice_template_example.xlsx`

**Simple**

**S-CAEX-01**  
Q: What agreement number appears on the example workbook?  
A: `EPC-19-000`.  
Source: `california_energy_standard_invoice_template_example.xlsx` sheet `Invoice Payment Cover Sheet`

**S-CAEX-02**  
Q: What invoice number appears on the example workbook?  
A: `AAA-19-000-27`.  
Source: `california_energy_standard_invoice_template_example.xlsx` sheet `Invoice Payment Cover Sheet`

**S-CAEX-03**  
Q: What invoice date appears on the example workbook?  
A: `2021-08-10`.  
Source: `california_energy_standard_invoice_template_example.xlsx` sheet `Invoice Payment Cover Sheet`

**Moderate**

**M-CAEX-01**  
Q: What period is covered by the example invoice request?  
A: `07/01/2021 - 07/31/2021`.  
Source: `california_energy_standard_invoice_template_example.xlsx` sheets `Invoice Payment Cover Sheet` and `Invoice Summary`

**M-CAEX-02**  
Q: What email address is given for submitting invoices?  
A: `invoices@energy.ca.gov`.  
Source: `california_energy_standard_invoice_template_example.xlsx` sheet `Invoice Payment Cover Sheet`

**M-CAEX-03**  
Q: What is the total to pay on this invoice?  
A: `$674,761.67`.  
Source: `california_energy_standard_invoice_template_example.xlsx` sheet `Invoice Payment Cover Sheet`

**M-CAEX-04**  
Q: What California Energy Commission billing address is shown on the cover sheet?  
A: `California Energy Commission, Accounting Office, MS-2, 715 P Street, Sacramento, CA 95814`.  
Source: `california_energy_standard_invoice_template_example.xlsx` sheet `Invoice Payment Cover Sheet`

**Hard**

**H-CAEX-01**  
Q: What are the Agreement Reimbursable Budget and Reimbursable Expenses This Period for Direct Labor?  
A: Budget is `$164,250`, and expenses this period are `$8,365.01`.  
Source: `california_energy_standard_invoice_template_example.xlsx` sheet `Invoice Summary`

**H-CAEX-02**  
Q: What was the cumulative Equipment amount billed to date, and what reimbursable balance remained?  
A: `Cumulative billed to date: $566,591.73`; `reimbursable balance: $125,408.27`.  
Source: `california_energy_standard_invoice_template_example.xlsx` sheet `Invoice Summary`

**H-CAEX-03**  
Q: What were the total reimbursable expenses this period and total cumulative expenses billed to date?  
A: `This period: $674,761.67`; `cumulative billed to date: $851,243.73`.  
Source: `california_energy_standard_invoice_template_example.xlsx` sheet `Invoice Summary`

**H-CAEX-04**  
Q: Which category had the largest reimbursable expenses this period in the example summary?  
A: `Equipment`, with `$549,553.89` in reimbursable expenses this period.  
Source: `california_energy_standard_invoice_template_example.xlsx` sheet `Invoice Summary`

**H-CAEX-05**  
Q: What were the reimbursable balances for Direct Labor and Fringe Benefits?  
A: `Direct Labor: $123,930.82`; `Fringe Benefits: $95,839.02`.  
Source: `california_energy_standard_invoice_template_example.xlsx` sheet `Invoice Summary`

**H-CAEX-06**  
Q: What were the Agreement Match Share Budget and Match Share Expenses This Period for Equipment?  
A: `Agreement Match Share Budget: $93,000`; `Match Share Expenses This Period: $157,865.67`.  
Source: `california_energy_standard_invoice_template_example.xlsx` sheet `Invoice Summary`

**H-CAEX-07**  
Q: What certification statement does the authorized representative make on the cover sheet?  
A: They certify under penalty of perjury that the invoice is accurate, conforms to the agreement, reimbursement has not been and will not be received from other sources, backup documentation is available on request, and all invoiced amounts are actual and allowable expenditures.  
Source: `california_energy_standard_invoice_template_example.xlsx` sheet `Invoice Payment Cover Sheet`

---

## `applied-jobs-april24.xlsx`

**Simple**

**S-JOBSXL-01**  
Q: What is the first column name in the applied jobs workbook?  
A: `Job Title`.  
Source: `applied-jobs-april24.xlsx` sheet `Sheet1`

**S-JOBSXL-02**  
Q: What company is listed for the first job entry?  
A: `Michael Page`.  
Source: `applied-jobs-april24.xlsx` sheet `Sheet1`, first data row

**S-JOBSXL-03**  
Q: What location is listed for the first job entry?  
A: `UAE`.  
Source: `applied-jobs-april24.xlsx` sheet `Sheet1`, first data row

**Moderate**

**M-JOBSXL-01**  
Q: What status is shown for the first several visible applications?  
A: They are shown as `Under Review`.  
Source: `applied-jobs-april24.xlsx` sheet `Sheet1`, first visible rows

**M-JOBSXL-02**  
Q: What note is attached to the first Michael Page entry?  
A: `Summary for Phone Interview`.  
Source: `applied-jobs-april24.xlsx` sheet `Sheet1`, first data row

**M-JOBSXL-03**  
Q: What application deadline is listed for the first Michael Page entry?  
A: `2 Weeks`.  
Source: `applied-jobs-april24.xlsx` sheet `Sheet1`, first data row

**Hard**

**H-JOBSXL-01**  
Q: Among the first five visible job entries, how many are located in the UAE?  
A: `Three`.  
Source: `applied-jobs-april24.xlsx` sheet `Sheet1`, first five data rows

**H-JOBSXL-02**  
Q: Which company is attached to the `AVP Relationship Manager` role?  
A: `Michael Page`.  
Source: `applied-jobs-april24.xlsx` sheet `Sheet1`, first data row

**H-JOBSXL-03**  
Q: Among the first six visible rows, which countries appear in the location column?  
A: `UAE`, `Saudi Arabia`, `Qatar`, and `Oman`.  
Source: `applied-jobs-april24.xlsx` sheet `Sheet1`, first six visible data rows

**H-JOBSXL-04**  
Q: Which first visible job entry includes a named contact person, and who is it?  
A: The `AVP Relationship Manager` entry at `Michael Page`, and the contact person is `Zaynah Aboobaker`.  
Source: `applied-jobs-april24.xlsx` sheet `Sheet1`, first data row

---

## `jobs_sheet.csv`

**Simple**

**S-JOBSCSV-01**  
Q: What are the five column headers in `jobs_sheet.csv`?  
A: `company`, `title`, `location`, `category`, `job_id`.  
Source: `jobs_sheet.csv` header row

**S-JOBSCSV-02**  
Q: What is the job ID for the Senior Product Manager role at Michael Page?  
A: `JOB-001`.  
Source: `jobs_sheet.csv` row 2

**Moderate**

**M-JOBSCSV-01**  
Q: Which company has the `Data Analyst` role, and where is it located?  
A: `Acme Consulting` in `Chicago`.  
Source: `jobs_sheet.csv` row 3

**M-JOBSCSV-02**  
Q: What category is assigned to the Registered Nurse role at Helio Health Clinic?  
A: `Healthcare`.  
Source: `jobs_sheet.csv` row 4

**Hard**

**H-JOBSCSV-01**  
Q: Which listed role is based in Detroit, and what is its job ID?  
A: `Maintenance Supervisor` at `Atlas Manufacturing`, job ID `AT-M-77`.  
Source: `jobs_sheet.csv` row 5

**H-JOBSCSV-02**  
Q: Which row represents a healthcare job, and where is it located?  
A: `Registered Nurse` at `Helio Health Clinic`, located in `Austin`.  
Source: `jobs_sheet.csv` row 4

**H-JOBSCSV-03**  
Q: Which listed job belongs to the Product category, and where is it located?  
A: `Senior Product Manager` at `Michael Page`, located in `London`.  
Source: `jobs_sheet.csv` row 2

---

## `purchasing-sales-data.csv`

**Simple**

**S-SALESCSV-01**  
Q: What is the main product identifier column called in the sales CSV?  
A: `Product Code`.  
Source: `purchasing-sales-data.csv` header row

**S-SALESCSV-02**  
Q: What is the first visible product code in the file snapshot?  
A: `33273`.  
Source: `purchasing-sales-data.csv` first visible product row

**Moderate**

**M-SALESCSV-01**  
Q: What is the first visible Arabic product name associated with product code `33273`?  
A: `ابيوبروسول جيل 100 جرام`.  
Source: `purchasing-sales-data.csv` first visible product row

**M-SALESCSV-02**  
Q: What is another visible product code that appears after `33273` in the snapshot?  
A: `34782` or `20667`.  
Source: `purchasing-sales-data.csv` next visible product rows

**Hard**

**H-SALESCSV-01**  
Q: Give two examples of English channel/account headers that appear in the file.  
A: Any two of: `Private Hospi Alex`, `Private Hospi Cairo`, `Key Accounts W Cairo`, `Key Accounts W Giza`, `October K.Acc`, `Helwan K.Acc`, `Retail October`.  
Source: `purchasing-sales-data.csv` header row

**H-SALESCSV-02**  
Q: Give two examples of Arabic channel/account headers that appear in the file.  
A: Any two of: `ك.عملاء اسكندرية 1`, `ك.عملاء اسكندرية2`, `ك.عملاء القناة`, `ك.عملاء وسط البلد`, `ك.عملاء امبابة`, `ك.عملاء المنوفية`.  
Source: `purchasing-sales-data.csv` header row

**H-SALESCSV-03**  
Q: In the visible sample, what Arabic product name is paired with product code `34782`?  
A: `ازموراب 10 مجم 7 اكياس`.  
Source: `purchasing-sales-data.csv` visible product row for code `34782`

**H-SALESCSV-04**  
Q: Name two English retail headers that appear in the sales CSV.  
A: Any two of: `Retail October`, `Retail SUEZ`, `Retail Miami`, `Retail Imbaba`, `Retail Mostorod`.  
Source: `purchasing-sales-data.csv` header row
