/**
 * ==========================================================================
 * 라벨 템플릿 렌더러
 * ==========================================================================
 */

const LabelTemplates = {
    renderShipmentLabel(item) {
        const customerName = item.customer || "(주)유라";
        const partNo = item.part_no || "";
        const partName = (item.part_name || "").replace(/\n/g, '<br>');
        const dateStr = item.delivery_date || "";
        const qtyDisplay = typeof item.qty === "number" ? item.qty.toLocaleString() : (item.qty || "");
        const rawSerial = item.serial || "";
        const rawBarcode = item.barcode || `P${partNo}Q${item.qty}S${rawSerial}`;

        const svgBarcode = Code128Engine.createSVG(rawBarcode);

        return `
        <div class="label-page">
            <div class="label-box">
                <div class="table-wrapper">
                    <table class="company-table">
                        <tr>
                            <td>(주)유니코어텍</td>
                            <td>${customerName}</td>
                        </tr>
                    </table>

                    <table class="data-table-main">
                        <tr>
                            <td class="col-header">품 번</td>
                            <td class="col-value">${partNo}</td>
                            <td class="col-side-header">납품일자</td>
                        </tr>
                        <tr>
                            <td class="col-header">품 명</td>
                            <td class="col-value"><div class="part-name-text">${partName}</div></td>
                            <td class="col-side-header">${dateStr}</td>
                        </tr>
                        <tr>
                            <td class="col-header">수 량</td>
                            <td class="col-value" style="font-size: 10pt;">${qtyDisplay}</td>
                            <td class="col-side-header">출하검사</td>
                        </tr>
                        <tr>
                            <td class="col-header">SERIAL</td>
                            <td class="col-value"><div class="serial-text">${rawSerial}</div></td>
                            <td class="col-side-header">합 &nbsp; &nbsp; 격</td>
                        </tr>
                    </table>
                </div>

                <div class="barcode-section">
                    ${svgBarcode}
                    <div class="barcode-text">${rawBarcode}</div>
                    <div class="origin-text">MADE IN KOREA</div>
                </div>
            </div>
        </div>`;
    }
};