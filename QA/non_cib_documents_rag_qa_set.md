# Non-CIB Business Documents Manual QA Set

Generated: 2026-03-10 (Africa/Cairo)

This file is meant for fast copy/paste manual testing of the RAG system against the non-CIB corpus in `/documents/business/`.

Each QA includes:
- A question
- An expected answer grounded in the document text
- A source reference

Method note:
- This file was re-audited against the original source documents on disk.
- Questions here are intended to serve as source-of-truth checks for ingestion and portal quality, not to mirror existing ingested output.

Difficulty levels:
- **Simple**: direct fact lookup
- **Moderate**: compare, summarize, or combine multiple facts from one document
- **Hard**: calculation, synthesis, or structured reasoning grounded in one document

## Documents Covered

- `collin_college_employee_handbook_2024_2025.pdf`
- `gsa_lease_template_l100a_may_2025.pdf`
- `irs_form_w9_2024.pdf`
- `kentucky_standard_invoice.pdf`
- `maine_substitute_w9_vendor_authorization_form.pdf`
- `microsoft_2025_annual_report.docx`
- `microsoft_2025_shareholder_letter.docx`
- `montana_vendor_invoice_fillable.pdf`
- `opwdd_informational_contract_template.pdf`
- `salesforce_q4_fy21_earnings_presentation.pdf`
- `saudi-arabia-market-research.docx`
- `sba_sample_marketing_plan.pdf`
- `tennessee_stream_lease_template.pdf`
- `uscis_form_i9.pdf`
- `washington_employee_supplier_registration_form.pdf`
- `wichita_sample_rfp.pdf`
- `wichita_vendor_registration_form.pdf`

---

## `sba_sample_marketing_plan.pdf`

**Simple**

**S-SBA-01**  
Q: What business is the sample marketing plan for?  
A: `J&K Auto Repair`.  
Source: `sba_sample_marketing_plan.pdf` p.1

**S-SBA-02**  
Q: According to the plan, what two customer groups make up J&K Auto Repair’s target base?  
A: `Local community members` and `drivers on the nearby highway`.  
Source: `sba_sample_marketing_plan.pdf` p.1

**S-SBA-03**  
Q: What percentage of customers does J&K expect from the local community?  
A: `80%`.  
Source: `sba_sample_marketing_plan.pdf` p.1

**S-SBA-04**  
Q: How many auto repair shops are within a 15-mile radius?  
A: `Four`.  
Source: `sba_sample_marketing_plan.pdf` p.2

**S-SBA-05**  
Q: What percentage of customers does J&K expect from drivers on the nearby highway?  
A: `20%`.  
Source: `sba_sample_marketing_plan.pdf` p.1

**Moderate**

**M-SBA-01**  
Q: What competitive advantage does J&K claim over most nearby competitors?  
A: It expects to be `one of the only local shops offering both auto repair services and auto parts for sale`.  
Source: `sba_sample_marketing_plan.pdf` p.1-p.2

**M-SBA-02**  
Q: What are the four marketing budget allocations listed in the plan?  
A: `Billboard: $500`, `local TV station digital ads: $200`, `local newspaper digital/print ads: $200`, `university newspaper digital/print ads: $100`.  
Source: `sba_sample_marketing_plan.pdf` p.2

**M-SBA-03**  
Q: Which two shops in the local market offer both repair services and parts for sale?  
A: `J&K Auto Repair` and `one other local competitor`; the plan says only those two shops offer both.  
Source: `sba_sample_marketing_plan.pdf` p.2

**Hard**

**H-SBA-01**  
Q: What is the total marketing budget, and what share of it is allocated to the billboard?  
A: Total budget is `$1,000`, and the billboard gets `$500`, which is `50%` of the total.  
Source: `sba_sample_marketing_plan.pdf` p.2

**H-SBA-02**  
Q: What evidence does the plan use to argue there is a strong local market for the shop?  
A: It says many local community members work for the nearby university and are well paid, half of university students have cars on campus, and the university is about `two hours` from the nearest major city, so many students drive long distances.  
Source: `sba_sample_marketing_plan.pdf` p.1

**H-SBA-03**  
Q: If J&K follows the plan exactly, what is the combined ad spend on local media versus the billboard?  
A: Local media totals `$500` (`$200` TV + `$200` newspaper + `$100` university newspaper), which matches the billboard spend of `$500`.  
Source: `sba_sample_marketing_plan.pdf` p.2

---

## `wichita_sample_rfp.pdf`

**Simple**

**S-RFP-01**  
Q: Through which department must all contact about the solicitation be made?  
A: The `Purchasing Department`.  
Source: `wichita_sample_rfp.pdf` p.1

**S-RFP-02**  
Q: What is the City Hall address listed in the RFP?  
A: `455 N Main, 12th Floor`.  
Source: `wichita_sample_rfp.pdf` p.1

**S-RFP-03**  
Q: What phone number is listed for the Purchasing Department?  
A: `316-268-4636`.  
Source: `wichita_sample_rfp.pdf` p.1

**S-RFP-04**  
Q: Who is identified as the Purchasing Manager?  
A: `Melinda Walker`.  
Source: `wichita_sample_rfp.pdf` p.1

**S-RFP-05**  
Q: What website is listed on the first page of the RFP?  
A: `https://ep.wichita.gov`.  
Source: `wichita_sample_rfp.pdf` p.1

**Moderate**

**M-RFP-01**  
Q: Why does the RFP say a lower price alone may not guarantee award?  
A: Because this document seeks `a solution` and differs from a simple bid or quotation, so `lowest price does not automatically determine award`.  
Source: `wichita_sample_rfp.pdf` p.2

**M-RFP-02**  
Q: After award, which response materials may be subject to public disclosure?  
A: `Proposal responses, purchase orders, and final contracts`.  
Source: `wichita_sample_rfp.pdf` p.2

**M-RFP-03**  
Q: How must questions, clarifications, and concerns be submitted?  
A: They must be submitted to the Purchasing Department `in writing`.  
Source: `wichita_sample_rfp.pdf` p.1

**Hard**

**H-RFP-01**  
Q: What two submission items are required in addition to the written proposal itself?  
A: `One original copy` and `one electronic copy in PDF or Word format on a flash drive`.  
Source: `wichita_sample_rfp.pdf` p.3

**H-RFP-02**  
Q: What kind of proposer behavior can lead to disqualification according to page 1?  
A: Contacting `city employees, department heads, using agencies, evaluation committee members, or elected officials` outside the Purchasing Department process can disqualify the response.  
Source: `wichita_sample_rfp.pdf` p.1

**H-RFP-03**  
Q: What does the RFP imply about transparency after award versus during evaluation?  
A: During the solicitation, communication is tightly restricted to the Purchasing Department, but after award, proposal responses, purchase orders, and final contracts may become publicly disclosable.  
Source: `wichita_sample_rfp.pdf` p.1-p.2

---

## `washington_employee_supplier_registration_form.pdf`

**Simple**

**S-WA-01**  
Q: What are the two main uses of this Washington form?  
A: To `register for a new Washington Statewide Supplier ID` or `change an existing Supplier ID record`.  
Source: `washington_employee_supplier_registration_form.pdf` p.1

**S-WA-02**  
Q: What is required if someone is changing an existing record?  
A: The `Supplier Number` is required.  
Source: `washington_employee_supplier_registration_form.pdf` p.1

**S-WA-03**  
Q: What two payment options does the form describe?  
A: `Direct deposit` or `check in U.S. mail`.  
Source: `washington_employee_supplier_registration_form.pdf` p.1

**S-WA-04**  
Q: What email address should the form be sent to?  
A: `supplierforms@ofm.wa.gov`.  
Source: `washington_employee_supplier_registration_form.pdf` p.2

**S-WA-05**  
Q: What fax number is listed for submitting the Washington form?  
A: `(360) 664-3363`.  
Source: `washington_employee_supplier_registration_form.pdf` p.2

**Moderate**

**M-WA-01**  
Q: If the employee chooses direct deposit, what extra bank details are required?  
A: `Financial institution name`, `phone number`, `routing transit number`, and `account number`.  
Source: `washington_employee_supplier_registration_form.pdf` p.1-p.2

**M-WA-02**  
Q: How long does the form say direct deposit activation usually takes?  
A: `3 to 5 business days`.  
Source: `washington_employee_supplier_registration_form.pdf` p.2

**M-WA-03**  
Q: If the form is for a new registration versus a change, how does the supplier-number requirement differ?  
A: A new registration does not require an existing supplier number, but changing an existing record does.  
Source: `washington_employee_supplier_registration_form.pdf` p.1

**Hard**

**H-WA-01**  
Q: What submission methods are allowed for this form, and what fax number is listed?  
A: It can be submitted by `email`, `fax`, or `mail`, and the fax number is `(360) 664-3363`.  
Source: `washington_employee_supplier_registration_form.pdf` p.2

**H-WA-02**  
Q: What signature rule does the form enforce?  
A: It requires a `wet signature`; `electronic, inserted, or stamped signatures` are not accepted.  
Source: `washington_employee_supplier_registration_form.pdf` p.2

**H-WA-03**  
Q: What mailing address is listed for paper submission of the Washington form?  
A: `PO Box 41450, Olympia, WA 98504-1450`.  
Source: `washington_employee_supplier_registration_form.pdf` p.2

---

## `maine_substitute_w9_vendor_authorization_form.pdf`

**Simple**

**S-ME-01**  
Q: What is the purpose of Maine’s Substitute W-9 and Vendor Authorization Form?  
A: To `establish or update an account with the State of Maine’s accounting system`.  
Source: `maine_substitute_w9_vendor_authorization_form.pdf` p.1

**S-ME-02**  
Q: Who should complete this Maine form?  
A: Anyone who `will receive payment from the State of Maine` and/or is a `vendor providing services or goods` to the state.  
Source: `maine_substitute_w9_vendor_authorization_form.pdf` p.1

**S-ME-03**  
Q: What federal form does this Maine form replace?  
A: It replaces the `IRS W-9`.  
Source: `maine_substitute_w9_vendor_authorization_form.pdf` p.1

**S-ME-04**  
Q: What two type-of-request options are shown on the Maine form?  
A: `New Request` and `New Location/Additional Entry` and `Change` are listed as request options; one must be selected.  
Source: `maine_substitute_w9_vendor_authorization_form.pdf` p.1

**Moderate**

**M-ME-01**  
Q: For a Maine Clean Election Act candidate, whose SSN should be used as the taxpayer ID?  
A: The `candidate’s SSN`.  
Source: `maine_substitute_w9_vendor_authorization_form.pdf` p.2

**M-ME-02**  
Q: What taxpayer IDs should not be used for a Maine Clean Election Act candidate?  
A: The form says not to use the `treasurer’s SSN` or the `treasurer’s EIN`.  
Source: `maine_substitute_w9_vendor_authorization_form.pdf` p.2

**M-ME-03**  
Q: What does the form say about the legal name tied to the ID number?  
A: The legal name provided must be the one `filed with the IRS` and tied to the listed `SSN or FEIN`.  
Source: `maine_substitute_w9_vendor_authorization_form.pdf` p.1

**Hard**

**H-ME-01**  
Q: For Maine Clean Election Act candidates, what two matching rules are given for name and address?  
A: The `legal name` must match the candidate name used to obtain the SSN, and the `address` must match the candidate registration.  
Source: `maine_substitute_w9_vendor_authorization_form.pdf` p.2

**H-ME-02**  
Q: What electronic signature tools are explicitly accepted on this form?  
A: `Adobe` and `DocuSign`.  
Source: `maine_substitute_w9_vendor_authorization_form.pdf` p.2

**H-ME-03**  
Q: Why does the Maine form say it can replace the IRS W-9?  
A: It quotes the W-9 rule that if a requester gives a substantially similar form to request a TIN, the requester’s form may be used instead of Form W-9.  
Source: `maine_substitute_w9_vendor_authorization_form.pdf` p.1

---

## `wichita_vendor_registration_form.pdf`

**Simple**

**S-WVR-01**  
Q: What address is shown for the City of Wichita vendor registration form?  
A: `455 N. Main; 12th Floor, Wichita, KS 67202`.  
Source: `wichita_vendor_registration_form.pdf` p.1

**S-WVR-02**  
Q: What phone number is listed on the form?  
A: `316-268-4636`.  
Source: `wichita_vendor_registration_form.pdf` p.1

**S-WVR-03**  
Q: What fax numbers are listed on the form?  
A: `316-268-4656` and `316-219-6308`.  
Source: `wichita_vendor_registration_form.pdf` p.1

**S-WVR-04**  
Q: What must be included with the Wichita vendor registration?  
A: A completed `IRS Form W-9`.  
Source: `wichita_vendor_registration_form.pdf` p.1

**Moderate**

**M-WVR-01**  
Q: What are the preferred PO delivery options listed?  
A: `Paper (mail)` or `email`.  
Source: `wichita_vendor_registration_form.pdf` p.1

**M-WVR-02**  
Q: What are the preferred payment method options listed?  
A: `Check` or `Electronic Funds Transfer (ACH)`.  
Source: `wichita_vendor_registration_form.pdf` p.1

**M-WVR-03**  
Q: Which two contact roles are requested on the form besides general company information?  
A: `Sales Contact Person` and `Accounts Receivable Contact Person`.  
Source: `wichita_vendor_registration_form.pdf` p.1

**Hard**

**H-WVR-01**  
Q: What TIN guidance does the form give for sole proprietors versus corporations?  
A: Individuals, self-employed people, or sole proprietorships must provide either an `SSN or FEIN`, while other businesses such as corporations must provide their `FEIN`.  
Source: `wichita_vendor_registration_form.pdf` p.1

**H-WVR-02**  
Q: What two compliance steps are explicitly required around tax/vendor status?  
A: One `1099 vendor` option (`YES` or `NO`) must be selected, and a completed `IRS Form W-9` must be included.  
Source: `wichita_vendor_registration_form.pdf` p.1

**H-WVR-03**  
Q: What does the form require around TIN certification?  
A: The signer certifies under penalties of perjury that the payee’s `TIN is correct` and acknowledges the registration by initialing and signing.  
Source: `wichita_vendor_registration_form.pdf` p.1

---

## `kentucky_standard_invoice.pdf`

**Simple**

**S-KY-01**  
Q: What cabinet name appears on the Kentucky standard invoice?  
A: `Kentucky Transportation Cabinet`.  
Source: `kentucky_standard_invoice.pdf` p.1

**S-KY-02**  
Q: What form code appears on the invoice?  
A: `TC 31-519`.  
Source: `kentucky_standard_invoice.pdf` p.1

**S-KY-03**  
Q: Which division name appears in the header of the form?  
A: `Division of Accounts`.  
Source: `kentucky_standard_invoice.pdf` p.1

**Moderate**

**M-KY-01**  
Q: What does the invoice tell the vendor to do with copies of the invoice?  
A: It says to `send the invoice in duplicate directly to the billing address shown on the contract` and `retain another copy for the vendor’s files`.  
Source: `kentucky_standard_invoice.pdf` p.1

**M-KY-02**  
Q: What fields are listed under Section 3: Invoice Information?  
A: `Item #`, `Amount`, `Description`, `Quantity`, `Unit`, and `Unit Price`.  
Source: `kentucky_standard_invoice.pdf` p.1

**M-KY-03**  
Q: What two sections identify the parties involved in the invoice?  
A: `Section 1: Delivery Information` and `Section 2: Vendor Information`.  
Source: `kentucky_standard_invoice.pdf` p.1

**Hard**

**H-KY-01**  
Q: What components are shown near the totaling area of the invoice?  
A: `Subtotal (page 1)`, `Subtotal (page 2)`, `Discount (%)`, and `Net Amount`.  
Source: `kentucky_standard_invoice.pdf` p.1-p.2

**H-KY-02**  
Q: What does the vendor certify at the top of the Kentucky invoice?  
A: The vendor certifies that the listed commodities or services were furnished to the Commonwealth of Kentucky, that quality and prices conform to the proposal and purchase order or contract, and that payment has not already been received in whole or in part.  
Source: `kentucky_standard_invoice.pdf` p.1

---

## `montana_vendor_invoice_fillable.pdf`

**Simple**

**S-MT-01**  
Q: What is the document title on the Montana form?  
A: `Vendor Invoice`.  
Source: `montana_vendor_invoice_fillable.pdf` p.1

**S-MT-02**  
Q: What state is named on the invoice?  
A: `State of Montana`.  
Source: `montana_vendor_invoice_fillable.pdf` p.1

**S-MT-03**  
Q: What mailing address is shown for DNRC-CARDD on the form?  
A: `PO Box 201601, Helena, MT 59620-1601`.  
Source: `montana_vendor_invoice_fillable.pdf` p.1

**Moderate**

**M-MT-01**  
Q: What does the vendor certify by signing the invoice?  
A: The vendor certifies that the invoice is `correct in all respects` and that `payment has not been received`.  
Source: `montana_vendor_invoice_fillable.pdf` p.1

**M-MT-02**  
Q: Name three project-related fields shown on the form.  
A: Any three of: `Project Title`, `Grant Agreement Number`, `Period of Performance`, `Reimbursement Request No.`, `Budget Category / Task Number and Description`.  
Source: `montana_vendor_invoice_fillable.pdf` p.1

**M-MT-03**  
Q: What fields appear in the state-use-only approval area?  
A: `Attn Grant Manager`, `Date Processed`, `Authorized Signature`, `Authorized Recipient Signature`, and `Authorized Recipient Name` appear around the approval area.  
Source: `montana_vendor_invoice_fillable.pdf` p.1

**Hard**

**H-MT-01**  
Q: What does the form instruct the vendor and the state to do with the signed original?  
A: It says `vendor returns signed original` and `file original with transfer-warrant claim`.  
Source: `montana_vendor_invoice_fillable.pdf` p.1

**H-MT-02**  
Q: What total field on the invoice indicates the amount requested for payment?  
A: `Grand Total`.  
Source: `montana_vendor_invoice_fillable.pdf` p.1

---

## `irs_form_w9_2024.pdf`

**Simple**

**S-W9-01**  
Q: What is the full title of Form W-9?  
A: `Request for Taxpayer Identification Number and Certification`.  
Source: `irs_form_w9_2024.pdf` p.1

**S-W9-02**  
Q: According to the form, where should you send the completed W-9?  
A: `Give form to the requester. Do not send to the IRS.`  
Source: `irs_form_w9_2024.pdf` p.1

**S-W9-03**  
Q: How many federal tax classification boxes are listed on page 1?  
A: `Seven`.  
Source: `irs_form_w9_2024.pdf` p.1

**S-W9-04**  
Q: What website does the form direct users to for instructions and the latest information?  
A: `www.irs.gov/FormW9`.  
Source: `irs_form_w9_2024.pdf` p.1

**Moderate**

**M-W9-01**  
Q: Name four information return forms listed on page 2.  
A: Any four of: `1099-INT`, `1099-DIV`, `1099-MISC`, `1099-NEC`, `1099-B`, `1099-S`, `1099-K`, `1098`, `1098-E`, `1098-T`, `1099-C`, `1099-A`.  
Source: `irs_form_w9_2024.pdf` p.2

**M-W9-02**  
Q: What can happen if you do not return Form W-9 with your TIN?  
A: You may be subject to `backup withholding`.  
Source: `irs_form_w9_2024.pdf` p.2

**M-W9-03**  
Q: For real estate transactions, do you have to sign the certification?  
A: `Yes.` The form says you must sign the certification for real estate transactions.  
Source: `irs_form_w9_2024.pdf` p.5

**M-W9-04**  
Q: For a sole proprietor, what name goes on line 1 and what name goes on line 2?  
A: The individual owner’s name goes on `line 1`, and the business, trade, or DBA name goes on `line 2`.  
Source: `irs_form_w9_2024.pdf` p.3

**Hard**

**H-W9-01**  
Q: What two penalties are stated for TIN-related noncompliance and false information?  
A: `A $50 penalty` for failure to furnish a TIN, and `a $500 civil penalty` for false information regarding withholding.  
Source: `irs_form_w9_2024.pdf` p.3

**H-W9-02**  
Q: What phishing guidance does the IRS give on page 6?  
A: The IRS says it `does not initiate contact by email` and does not ask for `PINs, passwords, or similar secret access information` by email.  
Source: `irs_form_w9_2024.pdf` p.6

**H-W9-03**  
Q: What are the two different TIN-furnishing rules on page 5 for interest/dividend accounts opened before 1984 versus after 1983?  
A: For pre-1984 interest, dividend, and barter exchange accounts, you must provide the correct TIN but do `not` have to sign. For accounts opened after 1983, you must provide the correct TIN and `sign the certification` or backup withholding can apply.  
Source: `irs_form_w9_2024.pdf` p.5

---

## `uscis_form_i9.pdf`

**Simple**

**S-I9-01**  
Q: By when must an employee complete and sign Section 1 of Form I-9?  
A: `No later than the first day of employment, but not before accepting a job offer.`  
Source: `uscis_form_i9.pdf` p.1

**S-I9-02**  
Q: Within how many business days must the employer complete and sign Section 2?  
A: Within `three business days` after the employee’s first day of employment.  
Source: `uscis_form_i9.pdf` p.1

**S-I9-03**  
Q: Can the employer tell the employee which acceptable documents to present?  
A: `No.` Employees can choose which acceptable documentation to present.  
Source: `uscis_form_i9.pdf` p.1

**Moderate**

**M-I9-01**  
Q: What documentation combination is required for Section 2?  
A: `One selection from List A` or `a combination of one selection from List B and one from List C`.  
Source: `uscis_form_i9.pdf` p.1-p.2

**M-I9-02**  
Q: Name two examples of List A documents.  
A: Any two of: `U.S. Passport`, `U.S. Passport Card`, `Permanent Resident Card`, `Form I-766 Employment Authorization Document`.  
Source: `uscis_form_i9.pdf` p.2

**M-I9-03**  
Q: Name two examples of List B documents.  
A: Any two of: `driver’s license`, `school ID card with a photograph`, `voter’s registration card`, `U.S. military card`.  
Source: `uscis_form_i9.pdf` p.2

**M-I9-04**  
Q: Name two examples of List C documents.  
A: Any two of: `Social Security card` without disqualifying restrictions, `certified birth certificate`, `Native American tribal document`, `U.S. Citizen ID Card (Form I-197)`.  
Source: `uscis_form_i9.pdf` p.2

**Hard**

**H-I9-01**  
Q: What is Supplement A used for?  
A: It is used by any `preparer and/or translator` who assists an employee in completing `Section 1`.  
Source: `uscis_form_i9.pdf` p.3

**H-I9-02**  
Q: What is Supplement B used for?  
A: It is used for `reverification`, `rehire within three years`, or `proof of a legal name change`.  
Source: `uscis_form_i9.pdf` p.4

**H-I9-03**  
Q: If an employee does not present a List A document, what exact combination must they present instead?  
A: They must present `one List B document` and `one List C document`.  
Source: `uscis_form_i9.pdf` p.2

**H-I9-04**  
Q: What does Supplement A require from each preparer or translator who helped with Section 1?  
A: Each preparer or translator must complete, `sign`, and `date` a separate certification area, and the employer must retain the completed supplement with the employee’s Form I-9.  
Source: `uscis_form_i9.pdf` p.3

**H-I9-05**  
Q: Under Supplement B, what documents may an employee choose to show for reverification?  
A: Any acceptable `List A` or `List C` documentation showing continued employment authorization.  
Source: `uscis_form_i9.pdf` p.4

---

## `collin_college_employee_handbook_2024_2025.pdf`

**Simple**

**S-COLL-01**  
Q: What phone number is given for HR accessibility help?  
A: `(972) 985-3783`.  
Source: `collin_college_employee_handbook_2024_2025.pdf` p.1

**S-COLL-02**  
Q: What email address is given for HR accessibility help?  
A: `hr@collin.edu`.  
Source: `collin_college_employee_handbook_2024_2025.pdf` p.1

**S-COLL-03**  
Q: How often are board members elected?  
A: `Biennially`.  
Source: `collin_college_employee_handbook_2024_2025.pdf` p.10

**S-COLL-04**  
Q: How long are board members’ terms?  
A: `Six years`.  
Source: `collin_college_employee_handbook_2024_2025.pdf` p.10

**Moderate**

**M-COLL-01**  
Q: How are full-time employees and part-time staff paid?  
A: `Full-time staff, full-time faculty, adjuncts, and continuing education instructors are paid monthly`, while `part-time staff and students are paid bi-weekly`.  
Source: `collin_college_employee_handbook_2024_2025.pdf` p.20

**M-COLL-02**  
Q: What does the handbook say about overtime eligibility for professional employees and academic administrators?  
A: They are generally `classified as exempt` and are `not entitled to overtime compensation`.  
Source: `collin_college_employee_handbook_2024_2025.pdf` p.20

**M-COLL-03**  
Q: How much paid sick leave do full-time employees earn each month, and what is the maximum accrual?  
A: `Eight hours per month`, up to a maximum of `720 hours`.  
Source: `collin_college_employee_handbook_2024_2025.pdf` p.30

**M-COLL-04**  
Q: How much accrued sick leave may be used each fiscal year for medical or dental appointments or to help care for an extended family member?  
A: Up to `three days (24 hours)`.  
Source: `collin_college_employee_handbook_2024_2025.pdf` p.30

**Hard**

**H-COLL-01**  
Q: If a full-time employee starts from zero sick leave, how many months would it take to reach the 720-hour cap if they accrue the maximum rate and use none?  
A: `90 months`, because `720 / 8 = 90`.  
Source: `collin_college_employee_handbook_2024_2025.pdf` p.30

**H-COLL-02**  
Q: What is the reporting deadline for an arrest, indictment, conviction, or similar adjudication involving a felony or moral turpitude?  
A: The employee must notify their supervisor within `three calendar days`.  
Source: `collin_college_employee_handbook_2024_2025.pdf` p.50

**H-COLL-03**  
Q: During an evacuation, how far away should people stay from the building, and what number should be called in a lockdown if information must be relayed to campus police?  
A: Stay at least `300 feet` away, and call `(972) 578-5555` or extension `5555`.  
Source: `collin_college_employee_handbook_2024_2025.pdf` p.60

**H-COLL-04**  
Q: Who are the student Title IX contacts named in the handbook?  
A: `Terrence Brennan` and `Amy Throop`.  
Source: `collin_college_employee_handbook_2024_2025.pdf` p.70

---

## `gsa_lease_template_l100a_may_2025.pdf`

**Simple**

**S-GSA-01**  
Q: What does the instruction to offeror say about completing the lease template?  
A: `Do not attempt to complete this lease template.`  
Source: `gsa_lease_template_l100a_may_2025.pdf` p.1

**S-GSA-02**  
Q: Which form will GSA use to transcribe the apparent lowest offeror’s final offered rent and price data?  
A: `GSA Lease Proposal Form 1364`.  
Source: `gsa_lease_template_l100a_may_2025.pdf` p.1

**Moderate**

**M-GSA-01**  
Q: How will the lease commencement date and termination/renewal rights be set more specifically?  
A: In a `Lease Amendment upon substantial completion and acceptance of the Space by the Government`.  
Source: `gsa_lease_template_l100a_may_2025.pdf` p.2

**M-GSA-02**  
Q: How is rent payable under the lease?  
A: By `electronic funds transfer` using the information in `SAM`.  
Source: `gsa_lease_template_l100a_may_2025.pdf` p.6

**Hard**

**H-GSA-01**  
Q: If tenant improvement cost exceeds the identified amount, what three options does the lease text provide?  
A: The Government may `reduce the tenant improvement requirements`, `pay the lump sum associated with the increase upon completion`, or `negotiate an increase in rent`.  
Source: `gsa_lease_template_l100a_may_2025.pdf` p.7

**H-GSA-02**  
Q: What does BSAC refer to in the lease?  
A: `Building Specific Amortized Capital`, used for `security-related improvements`.  
Source: `gsa_lease_template_l100a_may_2025.pdf` p.7

**H-GSA-03**  
Q: According to the schedule for completion of space, when are DIDs due?  
A: Within `XX Working Days from award`.  
Source: `gsa_lease_template_l100a_may_2025.pdf` p.23

**H-GSA-04**  
Q: What does the lease say about free rent components?  
A: Free rent includes `shell`, `operating`, `tenant improvement`, `BSAC`, and `parking` rent.  
Source: `gsa_lease_template_l100a_may_2025.pdf` p.6

**H-GSA-05**  
Q: What rights are included under express appurtenant rights besides parking?  
A: The Government gets rights to use `Appurtenant Areas`, roof space for `telecommunications equipment`, `roof access`, and necessary building areas such as `chases` and `plenums`.  
Source: `gsa_lease_template_l100a_may_2025.pdf` p.5

**H-GSA-06**  
Q: After the firm term, how can the Government terminate the lease under the termination-rights clause?  
A: It may terminate the lease in whole or in part `after the Firm Term` by giving `not less than XX days’ prior written notice` to the Lessor.  
Source: `gsa_lease_template_l100a_may_2025.pdf` p.8

---

## `opwdd_informational_contract_template.pdf`

**Simple**

**S-OPW-01**  
Q: Which New York agency is named in the contract template?  
A: `NYS OPWDD`.  
Source: `opwdd_informational_contract_template.pdf` p.1

**S-OPW-02**  
Q: How often will the contractor submit billings?  
A: `Monthly`.  
Source: `opwdd_informational_contract_template.pdf` p.2

**Moderate**

**M-OPW-01**  
Q: What document has highest priority in the stated hierarchy of precedent?  
A: `Appendix A`.  
Source: `opwdd_informational_contract_template.pdf` p.2

**M-OPW-02**  
Q: What is the order of precedence after Appendix A?  
A: `This agreement, including other appendices listed on page one`.  
Source: `opwdd_informational_contract_template.pdf` p.2

**M-OPW-03**  
Q: Who owns the work product created under the agreement?  
A: `OPWDD`.  
Source: `opwdd_informational_contract_template.pdf` p.3

**Hard**

**H-OPW-01**  
Q: What is the contract term stated in the template?  
A: It begins on `01 APRIL, 2015` and ends on `31 MARCH, 2020`.  
Source: `opwdd_informational_contract_template.pdf` p.3

**H-OPW-02**  
Q: What three early termination grounds does the State reserve?  
A: `Unavailability of funds`, `cause`, and `convenience`.  
Source: `opwdd_informational_contract_template.pdf` p.3

**H-OPW-03**  
Q: For a termination for convenience, what notice requirement is stated?  
A: The State must give written notice `30 days or more prior` to termination.  
Source: `opwdd_informational_contract_template.pdf` p.3

**H-OPW-04**  
Q: When must a request or notice for annual price adjustment be submitted?  
A: In writing between `30 days and 60 days prior` to the contract anniversary date or renewal date.  
Source: `opwdd_informational_contract_template.pdf` p.3

**H-OPW-05**  
Q: What billing-period minimum does the contract state for payments?  
A: Payments must cover a period of `not less than 30 days` and are paid after receipt of acceptable, properly documented bills.  
Source: `opwdd_informational_contract_template.pdf` p.2

**H-OPW-06**  
Q: What does the contract say about publication or dissemination of work based on the services rendered?  
A: It must be `kept in confidence` and may not be `released, published, or disseminated` without OPWDD’s express written consent.  
Source: `opwdd_informational_contract_template.pdf` p.3

---

## `tennessee_stream_lease_template.pdf`

**Simple**

**S-TNL-01**  
Q: In this lease template, who is the tenant?  
A: The `State of Tennessee`.  
Source: `tennessee_stream_lease_template.pdf` p.1-p.2

**S-TNL-02**  
Q: What body must approve changes to the template?  
A: The `State Building Commission`.  
Source: `tennessee_stream_lease_template.pdf` p.1

**S-TNL-03**  
Q: How many renewal options are shown on the lease term line?  
A: `One renewal option`.  
Source: `tennessee_stream_lease_template.pdf` p.2

**Moderate**

**M-TNL-01**  
Q: Under what condition may the Attorney General signature block be deleted?  
A: If `annual rent is under $50,000` or the `term is less than 5 years`.  
Source: `tennessee_stream_lease_template.pdf` p.3

**M-TNL-02**  
Q: What two forms must be received before payment will be made?  
A: An `IRS W-9` and a `Supplier Direct Deposit Authorization Form`.  
Source: `tennessee_stream_lease_template.pdf` p.4

**M-TNL-03**  
Q: If the landlord provides utilities, what availability requirement applies?  
A: Utilities must be available `24 hours a day, 7 days a week`.  
Source: `tennessee_stream_lease_template.pdf` p.5

**Hard**

**H-TNL-01**  
Q: What response times are required for routine maintenance calls versus emergency calls?  
A: `Routine calls within 24 hours`; `emergency calls within 4 hours`.  
Source: `tennessee_stream_lease_template.pdf` p.5

**H-TNL-02**  
Q: What temperature and humidity range is required for the telecommunications closet?  
A: Temperature `64 to 75 degrees` and relative humidity `30% to 55%`.  
Source: `tennessee_stream_lease_template.pdf` p.5

**H-TNL-03**  
Q: What liability insurance limits are required?  
A: `$1,000,000 per occurrence` and `$3,000,000 annual aggregate`.  
Source: `tennessee_stream_lease_template.pdf` p.5

**H-TNL-04**  
Q: According to the template, when does the lease commence?  
A: `30 days after substantial completion and issuance of the certificate of occupancy`.  
Source: `tennessee_stream_lease_template.pdf` p.10

**H-TNL-05**  
Q: What does the template say about the landlord’s legal authority to lease the premises?  
A: The landlord must be the `fee simple owner` of the property and have the right to lease it.  
Source: `tennessee_stream_lease_template.pdf` p.4

---

## `salesforce_q4_fy21_earnings_presentation.pdf`

**Simple**

**S-SFDC-01**  
Q: What was Salesforce’s FY21 GAAP revenue?  
A: `$21.2B`.  
Source: `salesforce_q4_fy21_earnings_presentation.pdf` p.5

**S-SFDC-02**  
Q: What was FY21 current remaining performance obligation (cRPO)?  
A: `$18.0B`.  
Source: `salesforce_q4_fy21_earnings_presentation.pdf` p.5

**S-SFDC-03**  
Q: What was FY21 GAAP diluted EPS?  
A: `$4.38`.  
Source: `salesforce_q4_fy21_earnings_presentation.pdf` p.5

**Moderate**

**M-SFDC-01**  
Q: What were FY21 GAAP versus non-GAAP operating margins?  
A: `GAAP operating margin: 2.1%`; `non-GAAP operating margin: 17.7%`.  
Source: `salesforce_q4_fy21_earnings_presentation.pdf` p.5

**M-SFDC-02**  
Q: What were Q4 FY21 GAAP revenue and non-GAAP revenue?  
A: `GAAP revenue: $5,763M`; `non-GAAP revenue: $5,817M`.  
Source: `salesforce_q4_fy21_earnings_presentation.pdf` p.10

**M-SFDC-03**  
Q: What were Q4 FY21 operating cash flow and operating cash flow growth?  
A: `$2,174M` and `33% growth`.  
Source: `salesforce_q4_fy21_earnings_presentation.pdf` p.10-p.15

**Hard**

**H-SFDC-01**  
Q: What was Salesforce’s FY21 total remaining performance obligation, and how much larger was it than cRPO?  
A: Total remaining performance obligation was `$36.1B`, which is `$18.1B` larger than cRPO of `$18.0B`.  
Source: `salesforce_q4_fy21_earnings_presentation.pdf` p.5

**H-SFDC-02**  
Q: What was the difference between FY21 non-GAAP diluted EPS and FY21 GAAP diluted EPS?  
A: `$0.54`, because non-GAAP diluted EPS was `$4.92` and GAAP diluted EPS was `$4.38`.  
Source: `salesforce_q4_fy21_earnings_presentation.pdf` p.5

**H-SFDC-03**  
Q: What FY22 non-GAAP diluted EPS guidance range was given?  
A: `$3.39 to $3.41`.  
Source: `salesforce_q4_fy21_earnings_presentation.pdf` p.25

**H-SFDC-04**  
Q: What was the difference between FY21 total remaining performance obligation and FY21 GAAP revenue?  
A: Total remaining performance obligation was `$36.1B` and GAAP revenue was `$21.2B`, so the difference was `$14.9B`.  
Source: `salesforce_q4_fy21_earnings_presentation.pdf` p.5

**H-SFDC-05**  
Q: What was the spread between Q4 FY21 non-GAAP diluted EPS and GAAP diluted EPS?  
A: `$0.76`, because non-GAAP diluted EPS was `$1.04` and GAAP diluted EPS was `$0.28`.  
Source: `salesforce_q4_fy21_earnings_presentation.pdf` p.10

---

## `saudi-arabia-market-research.docx`

**Simple**

**S-SAUDI-01**  
Q: What were the stated Saudi e-commerce revenues as of 2023?  
A: `$12.5 billion`.  
Source: `saudi-arabia-market-research.docx` para 2

**S-SAUDI-02**  
Q: What 2024-2028 revenue expectation is stated?  
A: `$21 billion`.  
Source: `saudi-arabia-market-research.docx` para 3

**S-SAUDI-03**  
Q: What CAGR is stated for the expected increase?  
A: `11% CAGR`.  
Source: `saudi-arabia-market-research.docx` para 4

**Moderate**

**M-SAUDI-01**  
Q: What share of total Saudi retail sales is attributed to e-commerce in the document, and what future share is projected by 2028?  
A: `7%` currently and `11%` projected by `2028`.  
Source: `saudi-arabia-market-research.docx` paras 6-7

**M-SAUDI-02**  
Q: What was the largest revenue leap described, and what caused it?  
A: Revenue increased by `65% in 2020 compared to 2019`, attributed to `COVID`.  
Source: `saudi-arabia-market-research.docx` para 9

**M-SAUDI-03**  
Q: What percentage of Saudi consumers said they support local e-commerce brands over famous platforms?  
A: `74%`.  
Source: `saudi-arabia-market-research.docx` para 15

**Hard**

**H-SAUDI-01**  
Q: What is the projected increase in e-commerce’s share of retail sales from now to 2028, in percentage points?  
A: `4 percentage points`, from `7%` to `11%`.  
Source: `saudi-arabia-market-research.docx` paras 6-7

**H-SAUDI-02**  
Q: What is the largest category listed, and what share does it have?  
A: `Electronics` at `23.3%`.  
Source: `saudi-arabia-market-research.docx` paras 19-20

**H-SAUDI-03**  
Q: What is the stated current Grocery share and projected 2028 Grocery share?  
A: `6.8%` currently and `10.8%` projected by `2028`.  
Source: `saudi-arabia-market-research.docx` para 26

**H-SAUDI-04**  
Q: Which two categories have the largest shares in the listed top categories, and what are those shares?  
A: `Electronics` at `23.3%` and `Hobby Leisure` at `22.7%`.  
Source: `saudi-arabia-market-research.docx` paras 20-21

---

## `microsoft_2025_annual_report.docx`

**Simple**

**S-MSANN-01**  
Q: What revenue did Microsoft report for the year, and by what percentage did it grow?  
A: `$281.7 billion`, up `15%`.  
Source: `microsoft_2025_annual_report.docx` para 8

**S-MSANN-02**  
Q: What operating income did Microsoft report, and by what percentage did it grow?  
A: `$128.5 billion`, up `17%`.  
Source: `microsoft_2025_annual_report.docx` para 8

**S-MSANN-03**  
Q: What milestone did Azure cross in revenue?  
A: Azure surpassed `$75 billion` in revenue.  
Source: `microsoft_2025_annual_report.docx` para 8

**Moderate**

**M-MSANN-01**  
Q: How many full-time engineers were dedicated to Secure Future Initiative work?  
A: The equivalent of `34,000` full-time engineers.  
Source: `microsoft_2025_annual_report.docx` para 18

**M-MSANN-02**  
Q: How many datacenters and regions does Microsoft say it operates?  
A: More than `400 datacenters` in `70 regions`.  
Source: `microsoft_2025_annual_report.docx` para 25

**M-MSANN-03**  
Q: How many paid customers does Microsoft Fabric have according to the report?  
A: `25,000 paid customers`.  
Source: `microsoft_2025_annual_report.docx` para 28

**M-MSANN-04**  
Q: What two in-house models did Microsoft introduce in addition to MAI-1 preview?  
A: `MAI-Voice-1` and `MAI-Image-1`.  
Source: `microsoft_2025_annual_report.docx` para 30

**M-MSANN-05**  
Q: How many monthly active users did Microsoft report across commercial and consumer Copilot?  
A: More than `100 million monthly active users`.  
Source: `microsoft_2025_annual_report.docx` para 32

**M-MSANN-06**  
Q: How many organizations use Copilot Studio according to the report?  
A: More than `230,000 organizations`.  
Source: `microsoft_2025_annual_report.docx` para 35

**Hard**

**H-MSANN-01**  
Q: In the `SHARE REPURCHASES AND DIVIDENDS` table, what were total repurchased shares and total amount for 2025?  
A: `31` shares and `$13,000` amount.  
Source: `microsoft_2025_annual_report.docx` table `SHARE REPURCHASES AND DIVIDENDS`, total row

**H-MSANN-02**  
Q: In the dividends table, what were total fiscal year 2025 dividends per share and total amount in millions?  
A: `Dividend per share: $3.32`; `amount in millions: $24,678`.  
Source: `microsoft_2025_annual_report.docx` table `Our Board of Directors declared the following dividends`, total row under `Fiscal Year 2025`

**H-MSANN-03**  
Q: In the 5-year cumulative total return table, what were Microsoft’s and the S&P 500’s values at `6/25`?  
A: `Microsoft: 255.13`; `S&P 500: 215.89`.  
Source: `microsoft_2025_annual_report.docx` table `COMPARISON OF 5 YEAR CUMULATIVE TOTAL RETURN*`

**H-MSANN-04**  
Q: According to the letter section embedded in the annual report, how many people will Microsoft’s skilling initiatives help earn AI credentials over the next two years?  
A: `20 million people`.  
Source: `microsoft_2025_annual_report.docx` para 42

**H-MSANN-05**  
Q: What numbers does the annual report give for Microsoft’s renewable energy procurement in 2020 versus 2024?  
A: It increased from `1.8 gigawatts` in `2020` to `34 gigawatts` in `2024`.  
Source: `microsoft_2025_annual_report.docx` para 50

**H-MSANN-06**  
Q: How many hours and how much money did Microsoft employees contribute to nonprofits this year?  
A: They volunteered over `1.2 million hours` and gave `$263 million` including company match.  
Source: `microsoft_2025_annual_report.docx` para 58

**H-MSANN-07**  
Q: As of June 30, 2025, how much remained under the $60.0 billion share repurchase program approved on September 16, 2024?  
A: `$57.3 billion` remained.  
Source: `microsoft_2025_annual_report.docx` para 82

**H-MSANN-08**  
Q: On July 24, 2025, how many registered holders of record of Microsoft common stock were there?  
A: `77,014`.  
Source: `microsoft_2025_annual_report.docx` para 78

---

## `microsoft_2025_shareholder_letter.docx`

**Simple**

**S-MSLTR-01**  
Q: What technology shift does the letter say Microsoft is in the middle of?  
A: The `AI platform shift`.  
Source: `microsoft_2025_shareholder_letter.docx` para 3

**S-MSLTR-02**  
Q: What three core business priorities are named in the letter?  
A: `Security`, `quality`, and `AI innovation`.  
Source: `microsoft_2025_shareholder_letter.docx` para 22

**Moderate**

**M-MSLTR-01**  
Q: What numbers does the letter give for LinkedIn members and gaming monthly active users?  
A: `LinkedIn: 1.2 billion members`; `gaming: 500 million monthly active users`.  
Source: `microsoft_2025_shareholder_letter.docx` para 38

**M-MSLTR-02**  
Q: What investment commitment does Microsoft Elevate make over the next five years?  
A: `$4 billion` in cash and AI cloud technology.  
Source: `microsoft_2025_shareholder_letter.docx` para 41

**M-MSLTR-03**  
Q: How many monthly active users did Microsoft say it surpassed across commercial and consumer Copilot?  
A: `100 million monthly active users`.  
Source: `microsoft_2025_shareholder_letter.docx` para 32

**M-MSLTR-04**  
Q: How many models does Azure AI Foundry include access to, and what share of the Fortune 500 uses Foundry?  
A: More than `11,000 models`, and `80% of the Fortune 500` use Foundry.  
Source: `microsoft_2025_shareholder_letter.docx` para 44

**M-MSLTR-05**  
Q: How many organizations use Copilot Studio according to the shareholder letter?  
A: More than `230,000 organizations`.  
Source: `microsoft_2025_shareholder_letter.docx` para 55

**Hard**

**H-MSLTR-01**  
Q: What annual financial performance summary is repeated in the shareholder letter?  
A: Revenue was `$281.7 billion` up `15%`, operating income grew `17%` to `$128.5 billion`, and Azure surpassed `$75 billion` in revenue, up `34%`.  
Source: `microsoft_2025_shareholder_letter.docx` para 9

**H-MSLTR-02**  
Q: Which named partners are explicitly mentioned for extending AI skilling opportunities?  
A: `UNICEF` and `Code.org`.  
Source: `microsoft_2025_shareholder_letter.docx` para 41

**H-MSLTR-03**  
Q: What two sustainability metrics does the letter give for renewable energy procurement and product packaging recyclability?  
A: Renewable energy procurement grew from `1.8 gigawatts` in `2020` to `34 gigawatts` in `2024`, and product packaging reached nearly `95% recyclability`.  
Source: `microsoft_2025_shareholder_letter.docx` para 84

**H-MSLTR-04**  
Q: What does the letter say about LinkedIn’s scale and gaming activity?  
A: LinkedIn is home to `1.2 billion members`, and gaming has `500 million monthly active users` across platforms and devices.  
Source: `microsoft_2025_shareholder_letter.docx` para 61
