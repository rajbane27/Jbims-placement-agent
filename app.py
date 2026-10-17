import streamlit as st
import os
import json
import sqlite3
import tempfile
from pypdf import PdfReader
from docx import Document
import google.generativeai as genai

# --- Page Configuration ---
st.set_page_config(
    page_title="Uppseekers Placement AI Agent",
    page_icon="🎯",
    layout="wide"
)

st.title("🎯 Uppseekers AI Placement & Resume Matcher")
st.markdown("Automated JD Screening & Candidate Ranking System (Connected to Form Submissions)")

# --- Sidebar Configuration ---
st.sidebar.header("🔑 Configuration")
api_key = st.sidebar.text_input("Google Gemini API Key", type="password")

if api_key:
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")

# --- Database Setup (SQLite for local/cloud persistence) ---
def init_db():
    conn = sqlite3.connect("placements.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_name TEXT,
            email TEXT UNIQUE,
            file_name TEXT,
            resume_text TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

# --- Helper Functions for Parsing ---
def extract_text_from_pdf(file_path):
    reader = PdfReader(file_path)
    text = ""
    for page in reader.pages:
        if page.extract_text():
            text += page.extract_text() + "\n"
    return text

def extract_text_from_docx(file_path):
    doc = Document(file_path)
    return "\n".join([para.text for para in doc.paragraphs])

# --- Main Layout ---
tab1, tab2 = st.tabs(["🚀 Run JD Matching", "📂 Manage Student Resumes DB"])

with tab2:
    st.subheader("Student Database Management")
    st.markdown("Upload batch resumes collected from your Google Form (or sync your Google Drive folder here).")
    
    uploaded_resumes = st.file_uploader(
        "Upload Student Resumes (.pdf or .docx)", 
        type=["pdf", "docx"], 
        accept_multiple_files=True
    )
    
    if st.button("Sync Resumes to Database"):
        if not uploaded_resumes:
            st.warning("Please upload at least one resume file.")
        else:
            conn = sqlite3.connect("placements.db")
            cursor = conn.cursor()
            success_count = 0
            
            for file in uploaded_resumes:
                file_extension = file.name.split('.')[-1].lower()
                with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_extension}") as tmp:
                    tmp.write(file.getvalue())
                    tmp_path = tmp.name
                
                try:
                    if file_extension == "pdf":
                        text = extract_text_from_pdf(tmp_path)
                    elif file_extension in ["docx", "doc"]:
                        text = extract_text_from_docx(tmp_path)
                    else:
                        text = ""
                    
                    # Using file name as a proxy identifier if name/email aren't pre-parsed
                    student_name = file.name.rsplit('.', 1)[0]
                    
                    cursor.execute("""
                        INSERT INTO students (student_name, email, file_name, resume_text)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(email) DO UPDATE SET
                        resume_text = excluded.resume_text,
                        file_name = excluded.file_name,
                        updated_at = CURRENT_TIMESTAMP
                    """, (student_name, f"{student_name}@student.com", file.name, text))
                    conn.commit()
                    success_count += 1
                except Exception as e:
                    st.error(f"Error processing {file.name}: {e}")
                finally:
                    os.unlink(tmp_path)
            
            conn.close()
            st.success(f"Successfully synced {success_count} resumes to the database!")

    # Display current DB stats
    conn = sqlite3.connect("placements.db")
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM students")
    total_count = cursor.fetchone()[0]
    conn.close()
    st.metric(label="Total Resumes in Database", value=total_count)

with tab1:
    st.subheader("Job Description (JD) Screening Portal")
    
    col1, col2 = st.columns(2)
    with col1:
        company_name = st.text_input("Company Name")
        role_title = st.text_input("Role Title")
        industry = st.selectbox("Industry", ["Finance / Banking", "Consulting", "Technology / IT", "Education", "FMCG / Retail", "Other"])

    with col2:
        offered_ctc = st.text_input("Offered CTC (e.g., 15 LPA)")
        jd_file = st.file_uploader("Upload Job Description File", type=["pdf", "docx"])

    if st.button("🔍 Run Matching Across All Resumes", type="primary"):
        if not api_key:
            st.error("Please provide your Gemini API key in the sidebar.")
        elif not jd_file:
            st.error("Please upload a Job Description file.")
        else:
            # Extract JD text
            file_extension = jd_file.name.split('.')[-1].lower()
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_extension}") as tmp:
                tmp.write(jd_file.getvalue())
                tmp_path = tmp.name
            
            jd_text = extract_text_from_pdf(tmp_path) if file_extension == "pdf" else extract_text_from_docx(tmp_path)
            os.unlink(tmp_path)

            # Fetch all students from DB
            conn = sqlite3.connect("placements.db")
            cursor = conn.cursor()
            cursor.execute("SELECT student_name, file_name, resume_text FROM students")
            students = cursor.fetchall()
            conn.close()

            if not students:
                st.warning("Your student database is empty! Please upload resumes in the 'Manage Student Resumes DB' tab first.")
            else:
                results = {
                    "Perfect Fit": [],
                    "80% Match": [],
                    "50% Match": [],
                    "Can Try for this Role": []
                }

                progress_bar = st.progress(0)
                status_text = st.empty()
                total_students = len(students)

                for idx, (s_name, f_name, r_text) in enumerate(students):
                    status_text.text(f"Screening candidate {idx+1} of {total_students}: {s_name}")

                    prompt = f"""
                    You are an expert recruitment manager for Uppseekers matching students to corporate roles.
                    Evaluate this candidate resume against the Job Description.

                    Job Details:
                    - Company: {company_name}
                    - Role: {role_title}
                    - Industry: {industry}
                    - Offered CTC: {offered_ctc}

                    Job Description:
                    {jd_text}

                    Candidate Resume:
                    {r_text}

                    Task:
                    Categorize this candidate strictly into one of these 4 categories:
                    1. "Perfect Fit"
                    2. "80% Match"
                    3. "50% Match"
                    4. "Can Try for this Role"

                    Return your response ONLY in valid JSON format with keys:
                    - "category": (Exact match to one of the 4 categories)
                    - "reason": (Concise 1-2 sentence justification)
                    """

                    try:
                        response = model.generate_content(prompt)
                        clean_response = response.text.strip().replace("```json", "").replace("```", "")
                        res_json = json.loads(clean_response)
                        cat = res_json.get("category", "Can Try for this Role")
                        
                        if cat in results:
                            results[cat].append({"name": s_name, "file": f_name, "reason": res_json.get("reason", "")})
                        else:
                            results["Can Try for this Role"].append({"name": s_name, "file": f_name, "reason": "Defaulted."})
                    except Exception as e:
                        results["Can Try for this Role"].append({"name": s_name, "file": f_name, "reason": f"Parsing error: {str(e)}"})

                    progress_bar.progress((idx + 1) / total_students)

                status_text.text("✅ Screening Complete!")

                # --- Display Results ---
                st.markdown("## 📊 Screening Outcomes")
                tabs = st.tabs(["🌟 Perfect Fit", "🔥 80% Match", "⚡ 50% Match", "🎯 Can Try for this Role"])
                categories_list = ["Perfect Fit", "80% Match", "50% Match", "Can Try for this Role"]

                for tab, cat_name in zip(tabs, categories_list):
                    with tab:
                        candidates = results[cat_name]
                        st.write(f"**Total Count:** {len(candidates)}")
                        for cand in candidates:
                            with st.expander(f"👤 {cand['name']} (File: {cand['file']})"):
                                st.write(f"**Why:** {cand['reason']}")
