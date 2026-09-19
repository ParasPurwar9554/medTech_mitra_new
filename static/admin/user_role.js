document.addEventListener("DOMContentLoaded", function () {

    const roleField = document.getElementById("id_role");
    const partnerField = document.querySelector(".field-knowledge_partner");

    // Stop if role field does not exist
    if (!roleField) {
        return;
    }

    function togglePartnerField() {

        if (!partnerField) {
            return;
        }

        if (roleField.value === "4") {
            partnerField.style.display = "";
        } else {
            partnerField.style.display = "none";

            const partnerSelect =
                document.getElementById("id_knowledge_partner");

            if (partnerSelect) {
                partnerSelect.value = "";
            }
        }
    }

    // Run when page loads
    togglePartnerField();

    // Run when role changes
    roleField.addEventListener("change", togglePartnerField);
});