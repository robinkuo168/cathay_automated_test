let selectedFiles = [];
const API_BASE = 'http://localhost:8000/api/es';
//const API_BASE = '/api/es'; // Set API base for Elasticsearch endpoints

// DOM Elements
const elements = {
    dropZone: document.querySelector('.drop-zone'),
    fileInput: document.getElementById('fileInput'),
    fileList: document.getElementById('fileList'),
    uploadBtn: document.getElementById('uploadBtn'),
    indexNameInput: document.getElementById('indexName'),
    deleteExistingSelect: document.getElementById('deleteExisting'),
    progressBar: document.getElementById('progressBar'),
    progressFill: document.getElementById('progressFill'),
    statusMessage: document.getElementById('statusMessage')
};

// Event Listeners
document.addEventListener('DOMContentLoaded', function() {
    if (!elements.dropZone) return; // Exit if not on the right page

    // Drag and drop events
    elements.dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        elements.dropZone.classList.add('dragover');
    });

    elements.dropZone.addEventListener('dragleave', () => {
        elements.dropZone.classList.remove('dragover');
    });

    elements.dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        elements.dropZone.classList.remove('dragover');
        handleFiles(e.dataTransfer.files);
    });

    // Click to select
    elements.dropZone.addEventListener('click', () => elements.fileInput.click());
    elements.fileInput.addEventListener('change', (e) => handleFiles(e.target.files));

    // Upload button
    elements.uploadBtn.addEventListener('click', uploadFiles);
});

function handleFiles(files) {
    const allowedTypes = ['.txt', '.xlsx', '.xls', '.yaml', '.yml'];

    Array.from(files).forEach(file => {
        const extension = '.' + file.name.split('.').pop().toLowerCase();

        if (allowedTypes.includes(extension)) {
            if (!selectedFiles.some(f => f.name === file.name && f.size === file.size)) {
                selectedFiles.push(file);
            }
        } else {
            showStatus(`檔案 "${file.name}" 的格式不受支援。`, 'error');
        }
    });

    updateFileList();
    updateUploadButton();
}

function updateFileList() {
    elements.fileList.innerHTML = '';

    selectedFiles.forEach((file, index) => {
        const extension = file.name.split('.').pop().toLowerCase();
        const fileItem = document.createElement('div');
        fileItem.className = 'file-item';

        fileItem.innerHTML = `
            <div class="file-info">
                <div class="file-icon ${extension}">${extension.toUpperCase()}</div>
                <div>
                    <div>${file.name}</div>
                    <small>${formatFileSize(file.size)}</small>
                </div>
            </div>
            <button class="remove-btn" onclick="removeFile(${index})">移除</button>
        `;

        elements.fileList.appendChild(fileItem);
    });
}

function removeFile(index) {
    selectedFiles.splice(index, 1);
    updateFileList();
    updateUploadButton();
}

function updateUploadButton() {
    elements.uploadBtn.disabled = selectedFiles.length === 0;
}

function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function showStatus(message, type) {
    elements.statusMessage.textContent = message;
    elements.statusMessage.className = `status-message status-${type}`;
    elements.statusMessage.style.display = 'block';

    setTimeout(() => {
        elements.statusMessage.style.display = 'none';
    }, 5000);
}

function showProgress(show) {
    elements.progressBar.style.display = show ? 'block' : 'none';
}

function updateProgress(percent) {
    elements.progressFill.style.width = percent + '%';
}

async function uploadFiles() {
    if (selectedFiles.length === 0) return;

    const indexName = elements.indexNameInput.value || 'cathay_project1_chunks';
    const deleteExisting = elements.deleteExistingSelect.value;

    elements.uploadBtn.disabled = true;
    elements.uploadBtn.textContent = '處理中...';
    showProgress(true);
    updateProgress(0);

    try {
        const formData = new FormData();
        selectedFiles.forEach(file => {
            formData.append('files', file);
        });
        formData.append('indexName', indexName);
        formData.append('deleteExisting', deleteExisting);

        const response = await fetch(`${API_BASE}/upload`, {
            method: 'POST',
            body: formData
        });

        const result = await response.json();

        if (!response.ok || !result.success) {
            throw new Error(result.error || result.message || '上傳失敗');
        }

        updateProgress(100);
        showStatus(`✅ 成功處理 ${selectedFiles.length} 個檔案至索引 "${indexName}"`, 'success');

        selectedFiles = [];
        updateFileList();

    } catch (error) {
        showStatus(`❌ 上傳失敗: ${error.message}`, 'error');
        console.error('Upload error:', error);
    } finally {
        elements.uploadBtn.disabled = false;
        elements.uploadBtn.textContent = '上傳至 Elasticsearch';
        showProgress(false);
        updateUploadButton();
    }
}