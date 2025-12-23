import os
from datetime import datetime
from flask import (
    Flask,
    render_template,
    request,
    send_file,
    jsonify
)
from dotenv import load_dotenv
from audit_engine import run_llm_audit, highlight_pdf

load_dotenv()

app = Flask(__name__)



BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")
REF_DIR = os.path.join(BASE_DIR, "reference_docs")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# -------------------------------------------------
# IN-MEMORY RUN REGISTRY
# -------------------------------------------------
# NOTE: Fine for single-instance dev / demo.
# For production → DB / Redis.
# -------------------------------------------------

RUNS = {}



@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload_pdf():
    pdf = request.files.get("pdf")

    if not pdf:
        return jsonify({"error": "No PDF uploaded"}), 400

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    input_pdf_path = os.path.join(
        UPLOAD_DIR,
        f"{run_id}_{pdf.filename}"
    )
    pdf.save(input_pdf_path)

    RUNS[run_id] = {
        "input_pdf": input_pdf_path
    }

    return jsonify({"run_id": run_id}), 200


@app.route("/run-audit/<run_id>", methods=["POST"])
def run_audit(run_id):
    if run_id not in RUNS:
        return "Invalid run ID", 404

    input_pdf_path = RUNS[run_id]["input_pdf"]

    output_excel_path = os.path.join(
        OUTPUT_DIR,
        f"audit_{run_id}.xlsx"
    )
    output_pdf_path = os.path.join(
        OUTPUT_DIR,
        f"audit_{run_id}.pdf"
    )

    try:
        final_data = run_llm_audit(
            ground_truth=os.path.join(REF_DIR, "RBI-KFS.pdf"),
            clm=os.path.join(REF_DIR, "CLM Guidelines1.pdf"),
            GL_regulation=os.path.join(REF_DIR, "New-Gold-Loan-Regulations1.pdf"),
            target_doc=input_pdf_path,
            user_prompt="",
            output_excel_path=output_excel_path
        )

        highlight_pdf(
            pdf_path=input_pdf_path,
            output_path=output_pdf_path,
            data=final_data
        )

        RUNS[run_id]["output_pdf"] = output_pdf_path
        RUNS[run_id]["output_excel"] = output_excel_path

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Audit failed: {str(e)}", 500

    return "", 200

# -------------------------------------------------
# RESULTS PAGE (SHOW DOWNLOAD BUTTONS)
# -------------------------------------------------

@app.route("/results/<run_id>")
def results(run_id):
    if run_id not in RUNS:
        return "Invalid run ID", 404

    return render_template("index.html", run_id=run_id)



# @app.route("/download/pdf/<run_id>")
# def download_pdf(run_id):
#     file_path = os.path.join(OUTPUT_DIR, f"audit_{run_id}.pdf")
#     return send_file(file_path, as_attachment=False)

@app.route("/download/pdf/<run_id>")
def download_pdf(run_id):
    if run_id not in RUNS or "output_pdf" not in RUNS[run_id]:
        return "File not ready", 404

    return send_file(
        RUNS[run_id]["output_pdf"],
        as_attachment=True,
        download_name=f"audit_{run_id}.pdf")

@app.route("/download/excel/<run_id>")
def download_excel(run_id):
    if run_id not in RUNS or "output_excel" not in RUNS[run_id]:
        return "File not ready", 404

    return send_file(
        RUNS[run_id]["output_excel"],
        as_attachment=True,
        download_name=f"audit_{run_id}.xlsx"
    )


# @app.route("/download/excel/<run_id>")
# def download_excel(run_id):
#     file_path = os.path.join(OUTPUT_DIR, f"audit_{run_id}.xlsx")
#     return send_file(file_path, as_attachment=False)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

    
