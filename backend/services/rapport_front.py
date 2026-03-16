import streamlit as st
import requests

st.title("📊 Générateur de Rapport d’Adéquation")

# Formulaire utilisateur
with st.form("rapport_form"):
    produit = st.text_input("Produit", "SCPI Darwin RE01")
    montant = st.number_input("Montant investi (€)", min_value=1000, step=1000, value=20000)
    profil = st.text_area("Profil client", "Investisseur prudent, souhaitant sécuriser son capital")

    submit = st.form_submit_button("🧠 Générer le rapport")

# Appel API
if submit:
    with st.spinner("Génération du rapport..."):
        response = requests.post(
            "http://127.0.0.1:5000/api/rapport",
            json={"produit": produit, "montant": montant, "profil_client": profil}
        )

        if response.status_code == 200:
            rapport = response.json()["rapport"]
            st.success("✅ Rapport généré")
            st.text_area("📄 Rapport d’adéquation", rapport, height=300)

            # Option export PDF
            from fpdf import FPDF

            class PDF(FPDF):
                def header(self):
                    self.set_font("Arial", "B", 12)
                    self.cell(0, 10, "Rapport d’adéquation - Darwin", ln=True, align="C")

                def footer(self):
                    self.set_y(-15)
                    self.set_font("Arial", "I", 8)
                    self.cell(0, 10, f"Page {self.page_no()}", align="C")

            pdf = PDF()
            pdf.add_page()
            pdf.set_font("Arial", size=12)
            for line in rapport.split("\n"):
                pdf.multi_cell(0, 10, line)

            pdf_file_path = "/tmp/rapport.pdf"
            pdf.output(pdf_file_path)

            with open(pdf_file_path, "rb") as f:
                st.download_button(
                    label="📥 Télécharger en PDF",
                    data=f,
                    file_name="rapport_dadequation.pdf",
                    mime="application/pdf"
                )
        else:
            st.error("❌ Erreur lors de la génération du rapport.")
            st.text(response.text)