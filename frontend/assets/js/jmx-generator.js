// frontend/jmx-generator.js
const API_BASE = 'http://localhost:8000';
let uploadedFiles = [];
let selectedFiles = [];
let jmxContent = '';

// DOM 元素
const elements = {
    fileInput: document.getElementById('fileInput'),
    uploadBtn: document.getElementById('uploadBtn'),
    clearFilesBtn: document.getElementById('clearFilesBtn'),
    fileList: document.getElementById('fileList'),
    uploadStatus: document.getElementById('uploadStatus'),
    requirements: document.getElementById('requirements'),
    generateBtn: document.getElementById('generateBtn'),
    validateBtn: document.getElementById('validateBtn'),
    resultSection: document.getElementById('resultSection'),
    jmxOutput: document.getElementById('jmxOutput'),
    downloadBtn: document.getElementById('downloadBtn'),
    copyBtn: document.getElementById('copyBtn'),
    loadingIndicator: document.getElementById('loadingIndicator'),
    statusBar: document.getElementById('statusBar'),
    statusText: document.getElementById('statusText'),
    charCount: document.getElementById('charCount')
};

// 初始化
document.addEventListener('DOMContentLoaded', function() {
    initializeEventListeners();
    updateCharCount();
    updateButtonStates();
});

function initializeEventListeners() {
    // 檔案選擇
    elements.fileInput.addEventListener('change', handleFileSelection);

    // 上傳按鈕
    elements.uploadBtn.addEventListener('click', uploadFiles);

    // 清除檔案按鈕
    elements.clearFilesBtn.addEventListener('click', clearSelectedFiles);

    // 需求輸入
    elements.requirements.addEventListener('input', function() {
        updateCharCount();
        updateButtonStates();
    });

    // 生成 JMX
    elements.generateBtn.addEventListener('click', generateJMX);

    // 驗證 JMX
    elements.validateBtn.addEventListener('click', validateJMX);

    // 下載按鈕
    elements.downloadBtn.addEventListener('click', downloadJMX);

    // 複製按鈕
    elements.copyBtn.addEventListener('click', copyJMX);

    // 拖拽上傳
    setupDragAndDrop();
}

function handleFileSelection(event) {
    const files = Array.from(event.target.files);

    // 過濾只允許 CSV 和 JSON 檔案
    const allowedExtensions = ['.csv', '.json'];
    const validFiles = [];
    const invalidFiles = [];

    files.forEach(file => {
        const fileName = file.name.toLowerCase();
        const isValid = allowedExtensions.some(ext => fileName.endsWith(ext));

        if (isValid) {
            validFiles.push(file);
        } else {
            invalidFiles.push(file.name);
        }
    });

    // 如果有無效檔案，顯示警告
    if (invalidFiles.length > 0) {
        showStatus(`❌ 不支援的檔案格式: ${invalidFiles.join(', ')}。僅支援 CSV 和 JSON 檔案`, 'error');
    }

    // 只保留有效檔案
    selectedFiles = validFiles;
    displaySelectedFiles();
    updateButtonStates();

    if (validFiles.length > 0) {
        showStatus(`已選擇 ${validFiles.length} 個有效檔案`, 'info');
    }
}

function displaySelectedFiles() {
    if (selectedFiles.length === 0) {
        elements.fileList.innerHTML = '';
        return;
    }

    const fileListHtml = selectedFiles.map(file => `
        <div class="file-item selected">
            <span class="file-name">${file.name}</span>
            <span class="file-size">${formatFileSize(file.size)}</span>
            <span class="file-status">待上傳</span>
        </div>
    `).join('');

    elements.fileList.innerHTML = `
        <h4>📋 已選擇的檔案:</h4>
        ${fileListHtml}
    `;
}

function clearSelectedFiles() {
    selectedFiles = [];
    elements.fileInput.value = '';
    elements.fileList.innerHTML = '';
    elements.uploadStatus.innerHTML = '';
    uploadedFiles = []; // 同時清除已上傳的檔案

    elements.requirements.value = '';
    updateCharCount(); // 更新字符計數
    
    // 清除 JMX 結果
    elements.resultSection.style.display = 'none';
    elements.jmxOutput.textContent = '';
    jmxContent = '';

    updateButtonStates();
    showStatus('已清除所有檔案選擇、測試需求和 JMX 結果', 'info');
}

async function uploadFiles() {
    if (selectedFiles.length === 0) {
        showStatus('請先選擇檔案', 'error');
        return;
    }

    showLoading(true);
    elements.uploadBtn.disabled = true;

    try {
        const formData = new FormData();
        selectedFiles.forEach(file => {
            formData.append('files', file);
        });

        showStatus('正在上傳檔案...', 'info');

        const response = await fetch(`${API_BASE}/upload`, {
            method: 'POST',
            body: formData
        });

        const result = await response.json();

        if (result.success) {
            uploadedFiles = result.data.files || [];
            showStatus(`✅ 成功上傳 ${result.data.processed} 個檔案`, 'success');
            displayUploadedFiles(result.data);

            // 清除已選擇的檔案（但保留上傳成功的記錄）
            selectedFiles = [];
            elements.fileInput.value = '';
        } else {
            showStatus(`❌ 上傳失敗: ${result.error}`, 'error');
        }

    } catch (error) {
        console.error('上傳錯誤:', error);
        showStatus(`❌ 上傳失敗: ${error.message}`, 'error');
    } finally {
        showLoading(false);
        updateButtonStates();
    }
}

function displayUploadedFiles(uploadData) {
    let html = '';

    // 成功上傳的檔案
    if (uploadData.files && uploadData.files.length > 0) {
        html += '<h4>✅ 已上傳檔案:</h4>';
        html += uploadData.files.map(file => `
            <div class="file-item uploaded">
                <span class="file-name">${file.filename}</span>
                <span class="file-size">${formatFileSize(file.size)}</span>
                <span class="file-status success">上傳成功</span>
            </div>
        `).join('');
    }

    // 失敗的檔案
    if (uploadData.failed_files && uploadData.failed_files.length > 0) {
        html += '<h4>❌ 上傳失敗:</h4>';
        html += uploadData.failed_files.map(file => `
            <div class="file-item failed">
                <span class="file-name">${file.filename}</span>
                <span class="file-error">${file.error}</span>
            </div>
        `).join('');
    }

    elements.uploadStatus.innerHTML = html;
}

async function generateJMX() {
    const requirements = elements.requirements.value.trim();

    if (!requirements) {
        showStatus('請輸入測試需求描述', 'error');
        return;
    }

    if (requirements.length < 10) {
        showStatus('需求描述至少需要 10 個字符', 'error');
        return;
    }

    showLoading(true);
    elements.generateBtn.disabled = true;

    try {
        showStatus('正在生成 JMX 檔案...', 'info');

        const requestData = {
            requirements: requirements,
            files: uploadedFiles.map(file => ({
                filename: file.filename,
                type: file.type,
                data: file.data
            }))
        };

        const response = await fetch(`${API_BASE}/generate-jmx`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(requestData)
        });

        const result = await response.json();

        if (result.success && result.data && result.data.content) {
            jmxContent = result.data.content;
            displayJMXResult(jmxContent);
            showStatus('✅ JMX 檔案生成成功！', 'success');
        } else {
            const errorMsg = result.error || result.message || '生成失敗';
            showStatus(`❌ 生成失敗: ${errorMsg}`, 'error');
        }

    } catch (error) {
        console.error('生成錯誤:', error);
        showStatus(`❌ 生成失敗: ${error.message}`, 'error');
    } finally {
        showLoading(false);
        updateButtonStates();
    }
}

async function validateJMX() {
    if (!jmxContent) {
        showStatus('請先生成 JMX 檔案', 'error');
        return;
    }

    showLoading(true);

    try {
        showStatus('正在驗證 JMX 格式...', 'info');

        const response = await fetch(`${API_BASE}/validate`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                xml_content: jmxContent
            })
        });

        const result = await response.json();

        if (result.success && result.data) {
            if (result.data.valid) {
                showStatus('✅ JMX 格式驗證通過！', 'success');
            } else {
                showStatus(`❌ JMX 格式驗證失敗: ${result.data.validation_message}`, 'error');
            }
        } else {
            showStatus(`❌ 驗證失敗: ${result.error}`, 'error');
        }

    } catch (error) {
        console.error('驗證錯誤:', error);
        showStatus(`❌ 驗證失敗: ${error.message}`, 'error');
    } finally {
        showLoading(false);
    }
}

function displayJMXResult(content) {
    elements.jmxOutput.textContent = content;
    elements.resultSection.style.display = 'block';
    elements.resultSection.scrollIntoView({ behavior: 'smooth' });
}

function downloadJMX() {
    if (!jmxContent) {
        showStatus('沒有可下載的內容', 'error');
        return;
    }

    const blob = new Blob([jmxContent], { type: 'application/xml' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `test-plan-${new Date().getTime()}.jmx`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    showStatus('✅ JMX 檔案已下載', 'success');
}

async function copyJMX() {
    if (!jmxContent) {
        showStatus('沒有可複製的內容', 'error');
        return;
    }

    try {
        await navigator.clipboard.writeText(jmxContent);
        showStatus('✅ 內容已複製到剪貼簿', 'success');
    } catch (error) {
        // 降級方案
        const textArea = document.createElement('textarea');
        textArea.value = jmxContent;
        document.body.appendChild(textArea);
        textArea.select();
        document.execCommand('copy');
        document.body.removeChild(textArea);
        showStatus('✅ 內容已複製到剪貼簿', 'success');
    }
}

function setupDragAndDrop() {
    const uploadArea = document.querySelector('.file-upload-area');

    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        uploadArea.addEventListener(eventName, preventDefaults, false);
    });

    function preventDefaults(e) {
        e.preventDefault();
        e.stopPropagation();
    }

    ['dragenter', 'dragover'].forEach(eventName => {
        uploadArea.addEventListener(eventName, highlight, false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
        uploadArea.addEventListener(eventName, unhighlight, false);
    });

    function highlight(e) {
        uploadArea.classList.add('drag-over');
    }

    function unhighlight(e) {
        uploadArea.classList.remove('drag-over');
    }

    uploadArea.addEventListener('drop', handleDrop, false);

    function handleDrop(e) {
        const dt = e.dataTransfer;
        const files = dt.files;

        // 使用相同的檔案驗證邏輯
        handleFileSelection({ target: { files: files } });
    }

}

// 修正：按鈕狀態更新邏輯
function updateButtonStates() {
    // 上傳相關按鈕
    const hasSelectedFiles = selectedFiles.length > 0;
    elements.uploadBtn.disabled = !hasSelectedFiles;
    elements.clearFilesBtn.disabled = selectedFiles.length === 0 && uploadedFiles.length === 0;

    // 生成按鈕 - 需要有需求描述，檔案是可選的
    const hasRequirements = elements.requirements.value.trim().length >= 10;
    elements.generateBtn.disabled = !hasRequirements;

    // 驗證按鈕 - 需要有生成的 JMX 內容
    elements.validateBtn.disabled = !jmxContent;
}

function updateCharCount() {
    const count = elements.requirements.value.length;
    elements.charCount.textContent = count;

    if (count > 10000) {
        elements.charCount.style.color = '#e74c3c';
    } else if (count > 8000) {
        elements.charCount.style.color = '#f39c12';
    } else {
        elements.charCount.style.color = '#7f8c8d';
    }
}

function showLoading(show) {
    elements.loadingIndicator.style.display = show ? 'flex' : 'none';
}

function showStatus(message, type = 'info') {
    elements.statusText.textContent = message;
    elements.statusBar.className = `status-bar ${type}`;

    // 自動清除狀態（除了錯誤訊息）
    if (type !== 'error') {
        setTimeout(() => {
            elements.statusText.textContent = '就緒';
            elements.statusBar.className = 'status-bar';
        }, 3000);
    }
}

function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';

    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));

    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

// 頁面載入時檢查 API 連接
window.addEventListener('load', async function() {
    try {
        const response = await fetch(`${API_BASE}/health`);
        const result = await response.json();

        if (result.success) {
            showStatus('✅ API 連接正常', 'success');
        } else {
            showStatus('⚠️ API 連接異常', 'warning');
        }
    } catch (error) {
        showStatus('❌ 無法連接到 API 服務', 'error');
        console.error('API 連接檢查失敗:', error);
    }
});