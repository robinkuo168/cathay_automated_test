// frontend/report-analyzer.js
//const API_BASE = 'http://localhost:8000';
const API_BASE ='/api';

let selectedReportFile = null;
let uploadedFile = null;

// 初始化
document.addEventListener('DOMContentLoaded', function() {
    console.log('DOM 載入完成，開始初始化...');
    initializeUploadZone();
    initializeFileInput();
    updateButtonStates();
    updateStatus('就緒');
});

function initializeUploadZone() {
    console.log('初始化上傳區域...');
    const uploadZone = document.getElementById('reportUploadZone');
    const uploadLabel = document.getElementById('uploadLabel');

    if (!uploadZone || !uploadLabel) {
        console.error('找不到上傳區域元素');
        return;
    }

    // 拖拽事件
    uploadZone.addEventListener('dragover', function(e) {
        e.preventDefault();
        e.stopPropagation();
        uploadZone.classList.add('drag-over');
    });

    uploadZone.addEventListener('dragleave', function(e) {
        e.preventDefault();
        e.stopPropagation();
        if (!uploadZone.contains(e.relatedTarget)) {
            uploadZone.classList.remove('drag-over');
        }
    });

    uploadZone.addEventListener('drop', function(e) {
        e.preventDefault();
        e.stopPropagation();
        uploadZone.classList.remove('drag-over');

        const files = e.dataTransfer.files;
        if (files.length > 0) {
            console.log('拖拽檔案:', files[0].name);
            handleFileSelection(files[0]);
        }
    });

    // 點擊上傳標籤觸發檔案選擇
    uploadLabel.addEventListener('click', function(e) {
        e.preventDefault();
        e.stopPropagation();
        console.log('點擊上傳標籤');

        const fileInput = document.getElementById('reportFileInput');
        if (fileInput) {
            // 重置檔案輸入，確保可以重新選擇相同檔案
            fileInput.value = '';
            fileInput.click();
        }
    });
}

function initializeFileInput() {
    console.log('初始化檔案輸入...');
    const fileInput = document.getElementById('reportFileInput');

    if (!fileInput) {
        console.error('找不到檔案輸入元素');
        return;
    }

    // 移除舊的事件監聽器（如果有的話）
    fileInput.removeEventListener('change', handleFileInputChange);

    // 添加新的事件監聽器
    fileInput.addEventListener('change', handleFileInputChange);
}

function handleFileInputChange(e) {
    console.log('檔案輸入變更事件觸發');
    console.log('選擇的檔案數量:', e.target.files.length);

    if (e.target.files.length > 0) {
        const file = e.target.files[0];
        console.log('選擇的檔案:', file.name, file.size, file.type);
        handleFileSelection(file);
    }
}

function handleFileSelection(file) {
    console.log('處理檔案選擇:', file.name);

    // 檢查檔案類型
    const allowedTypes = [
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/msword'
    ];

    const fileExtension = file.name.toLowerCase().split('.').pop();
    const allowedExtensions = ['docx', 'doc'];

    console.log('檔案類型:', file.type);
    console.log('檔案副檔名:', fileExtension);

    if (!allowedTypes.includes(file.type) && !allowedExtensions.includes(fileExtension)) {
        console.log('檔案類型不符合');
        showNotification('請選擇 Word 檔案 (.docx 或 .doc)', 'error');
        return;
    }

    // 檢查檔案大小 (限制 10MB)
    if (file.size > 10 * 1024 * 1024) {
        console.log('檔案太大');
        showNotification('檔案大小不能超過 10MB', 'error');
        return;
    }

    selectedReportFile = file;
    uploadedFile = null; // 重置上傳狀態

    displaySelectedFile(file);
    updateButtonStates();
    updateStatus(`已選擇檔案: ${file.name}`);
    showNotification(`已選擇檔案: ${file.name}`, 'success');

    console.log('檔案選擇處理完成');
}

function displaySelectedFile(file) {
    console.log('顯示選擇的檔案:', file.name);
    const fileList = document.getElementById('fileList');
    const selectedFiles = document.getElementById('selectedFiles');

    if (!fileList || !selectedFiles) {
        console.error('找不到檔案列表元素');
        return;
    }

    const fileHtml = `
        <div class="file-item selected" id="selectedFile">
            <div class="file-details">
                <span class="file-name">${file.name}</span>
                <span class="file-size">${formatFileSize(file.size)}</span>
            </div>
            <span class="file-status">已選擇</span>
            <button class="remove-file-btn" onclick="removeSelectedFile()">✕</button>
        </div>
    `;

    selectedFiles.innerHTML = fileHtml;
    fileList.style.display = 'block';
}

function removeSelectedFile() {
    console.log('移除選擇的檔案');
    selectedReportFile = null;
    uploadedFile = null;

    const fileList = document.getElementById('fileList');
    const selectedFiles = document.getElementById('selectedFiles');
    const fileInput = document.getElementById('reportFileInput');
    const previewSection = document.getElementById('previewSection');

    if (fileList) fileList.style.display = 'none';
    if (selectedFiles) selectedFiles.innerHTML = '';
    if (fileInput) fileInput.value = '';
    if (previewSection) previewSection.style.display = 'none';

    updateButtonStates();
    updateStatus('就緒');
    showNotification('已移除檔案', 'info');
}

function clearSelectedFiles() {
    console.log('清除選擇的檔案');
    removeSelectedFile();
}

async function uploadFile() {
    if (!selectedReportFile) {
        showNotification('請先選擇檔案', 'error');
        return;
    }

    console.log('開始上傳檔案:', selectedReportFile.name);

    try {
        showLoading('正在上傳檔案...');
        updateStatus('上傳中...');

        // 模擬上傳過程（這裡可以實際實現檔案上傳到伺服器）
        await new Promise(resolve => setTimeout(resolve, 1000));

        uploadedFile = selectedReportFile;

        // 更新檔案狀態顯示
        const fileItem = document.getElementById('selectedFile');
        if (fileItem) {
            fileItem.classList.remove('selected');
            fileItem.classList.add('uploaded');
            const statusSpan = fileItem.querySelector('.file-status');
            if (statusSpan) {
                statusSpan.textContent = '已上傳';
                statusSpan.classList.add('success');
            }
        }

        updateButtonStates();
        updateStatus(`檔案上傳完成: ${uploadedFile.name}`);
        showNotification('檔案上傳成功！', 'success');

        console.log('檔案上傳完成');
    } catch (error) {
        console.error('檔案上傳失敗:', error);
        showNotification(`上傳失敗: ${error.message}`, 'error');
        updateStatus('上傳失敗');
    } finally {
        hideLoading();
    }
}

function updateButtonStates() {
    const uploadBtn = document.getElementById('uploadBtn');
    const previewBtn = document.getElementById('previewBtn');
    const generateBtn = document.getElementById('generateBtn');

    if (!uploadBtn || !previewBtn || !generateBtn) {
        console.error('找不到按鈕元素');
        return;
    }

    // 上傳按鈕：有選擇檔案且未上傳時啟用
    uploadBtn.disabled = !selectedReportFile || uploadedFile;

    // 預覽和生成按鈕：檔案已上傳時啟用
    const hasUploadedFile = uploadedFile !== null;
    previewBtn.disabled = !hasUploadedFile;
    generateBtn.disabled = !hasUploadedFile;

    console.log('按鈕狀態更新:', {
        uploadBtn: !uploadBtn.disabled,
        previewBtn: !previewBtn.disabled,
        generateBtn: !generateBtn.disabled
    });
}

async function previewAnalysis() {
    if (!uploadedFile) {
        showNotification('請先上傳檔案', 'error');
        return;
    }

    const formData = new FormData();
    formData.append('file', uploadedFile);

    try {
        showLoading('正在分析報告...');
        updateStatus('分析中...');

        const response = await fetch(`${API_BASE}/preview-analysis`, {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            let errorMsg = `HTTP 錯誤！狀態碼: ${response.status}`;
            try {
                const errorData = await response.json();
                errorMsg = errorData.detail || errorData.message || errorMsg;
            } catch (e) {
                errorMsg = response.statusText;
            }
            throw new Error(errorMsg);
        }

        const analysisResult = await response.json();

        // 【關鍵修改】檢查 analysisResult.data 是否存在，並將其傳遞給顯示函式
        if (analysisResult && analysisResult.data && typeof analysisResult.data === 'object' && Object.keys(analysisResult.data).length > 0) {
            // 從 'data' 鍵中取出真正的分析物件
            displayAnalysisPreview(analysisResult.data); 
            showNotification('分析完成！', 'success');
            updateStatus('分析完成');
        } else {
            throw new Error('後端回傳的分析資料格式不正確或為空');
        }

    } catch (error) {
        console.error('預覽分析失敗:', error);
        showNotification(`分析失敗: ${error.message}`, 'error');
        updateStatus('分析失敗');
    } finally {
        hideLoading();
    }
}

async function generateAnalysisReport() {
    if (!uploadedFile) {
        showNotification('請先上傳檔案', 'error');
        return;
    }

    const formData = new FormData();
    formData.append('file', uploadedFile);

    try {
        showLoading('正在生成分析報告...');
        updateStatus('生成報告中...');

        const response = await fetch(`${API_BASE}/analyze-performance-report`, {
            method: 'POST',
            body: formData
        });

        if (response.ok) {
            // 獲取檔案名稱
            const contentDisposition = response.headers.get('Content-Disposition');
            let filename = `performance_analysis_${formatDateTime()}.docx`;

            if (contentDisposition) {
                const filenameMatch = contentDisposition.match(/filename="(.+)"/);
                if (filenameMatch) {
                    filename = filenameMatch[1];
                }
            }

            // 下載檔案
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);

            showNotification('分析報告已生成並下載！', 'success');
            updateStatus('報告已下載');
        } else {
            const errorData = await response.json();
            throw new Error(errorData.detail || errorData.message || '生成報告失敗');
        }
    } catch (error) {
        console.error('生成報告失敗:', error);
        showNotification(`生成失敗: ${error.message}`, 'error');
        updateStatus('生成失敗');
    } finally {
        hideLoading();
    }
}

function displayAnalysisPreview(analysis) {
    const previewSection = document.getElementById('previewSection');
    const previewContent = document.getElementById('previewContent');

    let html = '<div class="analysis-summary">';

    // TPS 分析
    if (analysis.tps_analysis) {
        const statusClass = analysis.tps_analysis.status === 'pass' ? 'status-pass' : 'status-fail';
        html += `
            <div class="analysis-item ${statusClass}">
                <h4>🎯 TPS 效能分析</h4>
                <p><strong>達標狀態:</strong> ${analysis.tps_analysis.status === 'pass' ? '✅ 通過' : '❌ 未達標'}</p>
                <p><strong>詳細分析:</strong> ${analysis.tps_analysis.details || 'N/A'}</p>
                ${analysis.tps_analysis.recommendations && analysis.tps_analysis.recommendations.length > 0 ?
                    `<p><strong>建議:</strong></p><ul>${analysis.tps_analysis.recommendations.map(rec => `<li>${rec}</li>`).join('')}</ul>` : ''}
            </div>
        `;
    }

    // 響應時間分析
    if (analysis.response_time_analysis) {
        const avgStatus = getStatusClass(analysis.response_time_analysis.avg_time_status);
        html += `
            <div class="analysis-item ${avgStatus}">
                <h4>⏱️ 響應時間分析</h4>
                <p><strong>平均響應時間:</strong> ${getStatusText(analysis.response_time_analysis.avg_time_status)}</p>
                <p><strong>99% 響應時間:</strong> ${getStatusText(analysis.response_time_analysis.p99_status)}</p>
                ${analysis.response_time_analysis.recommendations && analysis.response_time_analysis.recommendations.length > 0 ?
                    `<p><strong>建議:</strong></p><ul>${analysis.response_time_analysis.recommendations.map(rec => `<li>${rec}</li>`).join('')}</ul>` : ''}
            </div>
        `;
    }

    // 系統資源分析
    if (analysis.resource_analysis) {
        html += `
            <div class="analysis-item">
                <h4>🖥️ 系統資源分析</h4>
                <p><strong>CPU 建議:</strong> ${analysis.resource_analysis.cpu_recommendation || 'N/A'}</p>
                <p><strong>記憶體建議:</strong> ${analysis.resource_analysis.memory_recommendation || 'N/A'}</p>
                <p><strong>擴展建議:</strong> ${analysis.resource_analysis.scaling_suggestion || 'N/A'}</p>
            </div>
        `;
    }

    // 資料庫分析
    if (analysis.database_analysis) {
        const dbStatus = getStatusClass(analysis.database_analysis.performance_status);
        html += `
            <div class="analysis-item ${dbStatus}">
                <h4>🗄️ 資料庫分析</h4>
                <p><strong>效能狀態:</strong> ${getStatusText(analysis.database_analysis.performance_status)}</p>
                ${analysis.database_analysis.recommendations && analysis.database_analysis.recommendations.length > 0 ?
                    `<p><strong>建議:</strong></p><ul>${analysis.database_analysis.recommendations.map(rec => `<li>${rec}</li>`).join('')}</ul>` : ''}
            </div>
        `;
    }

    // 整體評估
    if (analysis.overall_assessment) {
        html += `
            <div class="analysis-item">
                <h4>📊 整體評估</h4>
                <p><strong>等級:</strong> ${analysis.overall_assessment.grade || 'N/A'}</p>
                <p><strong>摘要:</strong> ${analysis.overall_assessment.summary || 'N/A'}</p>
            </div>
        `;
    }

    // 額外測試建議
    if (analysis.additional_tests && analysis.additional_tests.length > 0) {
        html += `
            <div class="analysis-item">
                <h4>🧪 額外測試建議</h4>
                <ul>
                    ${analysis.additional_tests.map(test => `<li><strong>${test.scenario}:</strong> ${test.reason}</li>`).join('')}
                </ul>
            </div>
        `;
    }

    html += '</div>';
    previewContent.innerHTML = html;
    previewSection.style.display = 'block';

    // 滾動到預覽區域
    setTimeout(() => {
        previewSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 100);
}

// 輔助函數
function getStatusClass(status) {
    switch (status) {
        case 'good': return 'status-pass';
        case 'warning': return 'status-warning';
        case 'critical': return 'status-fail';
        default: return '';
    }
}

function getStatusText(status) {
    switch (status) {
        case 'good': return '✅ 良好';
        case 'warning': return '⚠️ 需關注';
        case 'critical': return '❌ 異常';
        default: return 'N/A';
    }
}

function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function formatDateTime() {
    const now = new Date();
    return now.getFullYear() +
           String(now.getMonth() + 1).padStart(2, '0') +
           String(now.getDate()).padStart(2, '0') + '_' +
           String(now.getHours()).padStart(2, '0') +
           String(now.getMinutes()).padStart(2, '0') +
           String(now.getSeconds()).padStart(2, '0');
}

function showLoading(message = '處理中...') {
    const loading = document.getElementById('loading');
    const loadingText = document.getElementById('loadingText');
    if (loading && loadingText) {
        loadingText.textContent = message;
        loading.style.display = 'flex';
    }
}

function hideLoading() {
    const loading = document.getElementById('loading');
    if (loading) {
        loading.style.display = 'none';
    }
}

function updateStatus(message) {
    const statusText = document.getElementById('statusText');
    if (statusText) {
        statusText.textContent = message;
    }
}

function showNotification(message, type = 'info') {
    const notification = document.getElementById('notification');
    if (!notification) return;

    const icon = notification.querySelector('.notification-icon');
    const messageEl = notification.querySelector('.notification-message');

    if (!icon || !messageEl) return;

    // 設定圖示
    switch (type) {
        case 'success':
            icon.textContent = '✅';
            break;
        case 'error':
            icon.textContent = '❌';
            break;
        case 'warning':
            icon.textContent = '⚠️';
            break;
        default:
            icon.textContent = 'ℹ️';
    }

    messageEl.textContent = message;
    notification.className = `notification-toast ${type}`;
    notification.style.display = 'block';

    // 5秒後自動隱藏
    setTimeout(() => {
        hideNotification();
    }, 5000);
}

function hideNotification() {
    const notification = document.getElementById('notification');
    if (notification) {
        notification.style.display = 'none';
    }
}

// 防止頁面刷新時的拖拽行為
document.addEventListener('dragover', function(e) {
    e.preventDefault();
});

document.addEventListener('drop', function(e) {
    e.preventDefault();
});

// 錯誤處理
window.addEventListener('error', function(e) {
    console.error('JavaScript 錯誤:', e.error);
    showNotification('發生未預期的錯誤，請重新整理頁面', 'error');
});

// 網路錯誤處理
window.addEventListener('unhandledrejection', function(e) {
    console.error('未處理的 Promise 拒絕:', e.reason);
    showNotification('網路請求失敗，請檢查連線', 'error');
});