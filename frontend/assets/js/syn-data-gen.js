//const API_BASE = 'http://localhost:8000/api';
const API_BASE = '/api';

// 全域變數
let selectedFile = null;
let uploadedFile = null;
let extractedText = '';
let headerJsonData = null;
let markdownTable = '';
let syntheticData = '';
let syntheticDataCsv = '';

// DOM 元素快取
const elements = {
    fileInput: document.getElementById('fileInput'),
    uploadBtn: document.getElementById('uploadBtn'),
    clearFilesBtn: document.getElementById('clearFilesBtn'),
    uploadZone: document.getElementById('uploadZone'),
    headerJson: document.getElementById('headerJson'),
    markdownTable: document.getElementById('markdownTable'),
    downloadHeaderBtn: document.getElementById('downloadHeaderBtn'),
    syntheticData: document.getElementById('syntheticData'),
    generateMarkdownBtn: document.getElementById('generateMarkdownBtn'),
    reviewSpecBtn: document.getElementById('reviewSpecBtn'),
    confirmMarkdownBtn: document.getElementById('confirmMarkdownBtn'),
    downloadSyntheticBtn: document.getElementById('downloadSyntheticBtn'),
    userFeedback: document.getElementById('userFeedback'),
    statusBar: document.getElementById('statusBar'),
    status: document.getElementById('status'),
    specAnalysisSection: document.getElementById('spec-analysis-section'),
    syntheticSection: document.getElementById('synthetic-section'),
    syntheticFeedback: document.getElementById('syntheticFeedback'),
    reviewSyntheticBtn: document.getElementById('reviewSyntheticBtn')
};

// 初始化 Header JSON 的 CodeMirror 編輯器
const headerEditor = CodeMirror.fromTextArea(elements.headerJson, {
    mode: { name: 'javascript', json: true },
    theme: 'monokai',
    lineNumbers: true,
    autoCloseBrackets: true,
    matchBrackets: true,
    indentUnit: 2,
    tabSize: 2,
    lineWrapping: true
});

// 初始化 Body Markdown 的 CodeMirror 編輯器
const markdownEditor = CodeMirror.fromTextArea(elements.markdownTable, {
    mode: 'markdown',
    theme: 'monokai',
    lineNumbers: true,
    autoCloseBrackets: true,
    matchBrackets: true,
    indentUnit: 2,
    tabSize: 2,
    lineWrapping: true
});

// 初始化合成資料的 CodeMirror 編輯器
const syntheticDataEditor = CodeMirror.fromTextArea(elements.syntheticData, {
    mode: 'markdown',
    theme: 'monokai',
    lineNumbers: true,
    readOnly: true,
    lineWrapping: true
});

// 頁面載入後執行的初始化函式
document.addEventListener('DOMContentLoaded', function() {
    console.log('DOM 載入完成，開始初始化...');
    initializeUploadZone();
    initializeFileInput();
    updateButtonStates();
    updateStatus('就緒');
});

function initializeUploadZone() {
    console.log('初始化上傳區域...');
    const uploadZone = document.getElementById('uploadZone');
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

        const fileInput = document.getElementById('fileInput');
        if (fileInput) {
            // 重置檔案輸入，確保可以重新選擇相同檔案
            fileInput.value = '';
            fileInput.click();
        }
    });
}

/**
 * 從 headerJsonData 中提取 MSGID 作為檔名。
 * @param {string} extension - 檔案的副檔名 (例如 'json' 或 'csv')。
 * @param {string} defaultFilename - 如果找不到 MSGID 時的預設檔名。
 * @returns {string} - 生成的檔名。
 */
function getFilenameFromMsgId(extension, defaultFilename) {
    // 如果沒有 header 資料，直接返回預設檔名
    if (!headerJsonData) {
        return defaultFilename;
    }

    // 處理 headerJsonData 可能是物件或陣列的情況
    const firstItem = Array.isArray(headerJsonData) ? headerJsonData[0] : headerJsonData;

    if (!firstItem || typeof firstItem !== 'object') {
        return defaultFilename;
    }

    // 增加對巢狀結構的支援，例如 { "MWHEADER": { "MSGID": "..." } }
    let msgId = null;

    // 策略一：直接在頂層尋找 MSGID
    if (firstItem.MSGID) {
        msgId = firstItem.MSGID;
    }
    // 策略二：如果頂層沒有，則在 MWHEADER 物件中尋找
    else if (firstItem.MWHEADER && typeof firstItem.MWHEADER === 'object' && firstItem.MWHEADER.MSGID) {
        msgId = firstItem.MWHEADER.MSGID;
    }

    // 檢查是否成功找到 MSGID
    if (msgId) {
        // 清理 MSGID，移除可能導致檔名無效的特殊字元
        const safeMsgId = String(msgId).replace(/[^a-z0-9_-]/gi, '_');
        return `${safeMsgId}.${extension}`;
    }

    // 如果找不到 MSGID，返回預設檔名
    return defaultFilename;
}

function initializeFileInput() {
    console.log('初始化檔案輸入...');
    const fileInput = document.getElementById('fileInput');

    if (!fileInput) {
        console.error('找不到檔案輸入元素');
        return;
    }

    fileInput.removeEventListener('change', handleFileInputChange);
    fileInput.addEventListener('change', handleFileInputChange);

    elements.uploadBtn.addEventListener('click', () => {
        if (selectedFile) uploadAndProcessFile(selectedFile);
    });

    elements.clearFilesBtn.addEventListener('click', removeSelectedFile);

    if (elements.generateMarkdownBtn) {
        elements.generateMarkdownBtn.addEventListener('click', () => {
            if (extractedText && selectedFile) {
                generateSpecAnalysis(extractedText, selectedFile.name);
            } else {
                showNotification('請先上傳並處理檔案', 'error');
            }
        });
    }

    if (elements.reviewSpecBtn) {
        elements.reviewSpecBtn.addEventListener('click', () => {
            console.log("--- 「校對 Markdown 規格」按鈕點擊事件觸發 ---");

            const feedback = elements.userFeedback.value;
            if (!feedback.trim()) {
                showNotification('請在「回饋或修改建議」欄位中輸入您的指令', 'warning');
                return;
            }

            const markdown = markdownEditor.getValue();
            if (markdown.trim().length > 0) {
                console.log("準備呼叫 reviewMarkdownTable API...");
                reviewMarkdownTable(markdown, feedback);
            } else {
                showNotification('沒有可校對的 Body (Markdown) 內容', 'error');
            }
        });
    }

    if (elements.confirmMarkdownBtn) {
        elements.confirmMarkdownBtn.addEventListener('click', () => {
            if (selectedFile) {
                generateSyntheticData(selectedFile.name);
            } else {
                showNotification('沒有可處理的檔案上下文', 'error');
            }
        });
    }

    // ▼▼▼【核心修正】▼▼▼
    if (elements.downloadHeaderBtn) {
        elements.downloadHeaderBtn.addEventListener('click', () => {
            if (headerJsonData) {
                // 使用輔助函式動態生成檔名
                const filename = getFilenameFromMsgId('json', 'api_examples.json');
                const dataToDownload = Array.isArray(headerJsonData) ? headerJsonData : [headerJsonData];
                const jsonString = JSON.stringify(dataToDownload, null, 2);
                downloadTextAsFile(jsonString, filename, 'application/json');
                showNotification(`結構化 JSON (${filename}) 下載開始`, 'success');
            } else {
                showNotification('沒有可下載的範例資料', 'error');
            }
        });
    }

    const downloadSyntheticBtn = document.getElementById('downloadSyntheticBtn');
    if (downloadSyntheticBtn) {
        downloadSyntheticBtn.addEventListener('click', () => {
            if (syntheticDataCsv) {
                // 使用輔助函式動態生成檔名
                const filename = getFilenameFromMsgId('csv', 'synthetic_data.csv');
                downloadTextAsFile(syntheticDataCsv, filename, 'text/csv');
                showNotification(`合成資料 CSV (${filename}) 下載開始`, 'success');
            } else {
                showNotification('沒有可下載的合成資料', 'error');
            }
        });
    }
    // ▲▲▲【核心修正】▲▲▲

    if (elements.userFeedback) {
        elements.userFeedback.addEventListener('input', updateButtonStates);
    }

    if (elements.reviewSyntheticBtn) {
        elements.reviewSyntheticBtn.addEventListener('click', () => {
            const currentData = syntheticDataEditor.getValue();
            const feedback = elements.syntheticFeedback.value;

            if (!feedback.trim()) {
                showNotification('請在「合成資料修改建議」欄位中輸入您的指令', 'warning');
                return;
            }
            if (!currentData.trim()) {
                showNotification('沒有可校對的合成資料', 'error');
                return;
            }
            reviewSyntheticData(currentData, feedback);
        });
    }

    if (elements.syntheticFeedback) {
        elements.syntheticFeedback.addEventListener('input', updateButtonStates);
    }
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

    selectedFile = file;
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
    selectedFile = null;
    uploadedFile = null;

    const fileList = document.getElementById('fileList');
    const selectedFiles = document.getElementById('selectedFiles');
    const fileInput = document.getElementById('fileInput');
    const specAnalysisSection = document.getElementById('spec-analysis-section');
    const syntheticSection = document.getElementById('synthetic-section');

    if (fileList) fileList.style.display = 'none';
    if (selectedFiles) selectedFiles.innerHTML = '';
    if (fileInput) fileInput.value = '';
    if (specAnalysisSection) specAnalysisSection.style.display = 'none';
    if (syntheticSection) syntheticSection.style.display = 'none';

    // 重置編輯器內容
    headerEditor.setValue('');
    markdownEditor.setValue('');
    syntheticDataEditor.setValue('');

    // 重置全域變數
    extractedText = '';
    headerJsonData = null;
    markdownTable = '';
    syntheticData = '';
    syntheticDataCsv = '';

    updateButtonStates();
    updateStatus('就緒');
    showNotification('已移除檔案', 'info');
}

async function uploadAndProcessFile(file) {
    updateStatus('正在上傳並處理檔案...');
    elements.uploadBtn.disabled = true;
    elements.clearFilesBtn.disabled = true;

    try {
        const formData = new FormData();
        formData.append('file', file);
        const response = await fetch(`${API_BASE}/process-docx`, { method: 'POST', body: formData });
        if (!response.ok) throw new Error(`HTTP 錯誤: ${response.status}`);

        const result = await response.json();
        const content = result.data?.text;
        if (result.success && typeof content === 'string') {
            extractedText = content;
            uploadedFile = file; // 設置 uploadedFile 變數
            updateStatus('檔案處理成功，請點擊「分析規格」按鈕');
            elements.generateMarkdownBtn.style.display = 'inline-block';
        } else {
            throw new Error(result.error || '回應結構不符，無法找到提取的文字');
        }
    } catch (error) {
        console.error('上傳錯誤:', error);
        updateStatus(`檔案處理失敗: ${error.message}`);
        alert(`檔案處理失敗: ${error.message}`);
    } finally {
        updateButtonStates();
        elements.clearFilesBtn.disabled = false;
    }
}

function activateSection(sectionId) {
    if (sectionId === 'spec') {
        // 啟用規格分析區塊時，確保合成資料區塊是隱藏的（以防是重新分析）
        elements.syntheticSection.style.display = 'none';
        elements.specAnalysisSection.style.display = 'block';
        setTimeout(() => {
            headerEditor.refresh();
            markdownEditor.refresh();
        }, 10);
    } else if (sectionId === 'synthetic') {
        // 啟用合成資料區塊時，規格分析區塊應保持可見
        elements.syntheticSection.style.display = 'block';
        setTimeout(() => syntheticDataEditor.refresh(), 10);
    }
    updateButtonStates();
}

function updateButtonStates() {
    const hasFile = !!selectedFile;
    const hasExtractedText = extractedText.trim().length > 0;
    const hasHeaderJson = headerJsonData !== null;
    const hasMarkdownTable = markdownTable.trim().length > 0;
    const isSpecAnalysisVisible = document.getElementById('spec-analysis-section')?.style.display !== 'none';
    const isSyntheticSectionVisible = elements.syntheticSection?.style.display !== 'none';
    const hasFeedback = elements.userFeedback.value.trim().length > 0;
    const hasSyntheticFeedback = elements.syntheticFeedback?.value.trim().length > 0;
    const hasSyntheticDataContent = syntheticData.trim().length > 0;

    // 上傳按鈕 - 當有選擇檔案且尚未處理完成時啟用
    if (elements.uploadBtn && !uploadedFile) {
        elements.uploadBtn.disabled = !hasFile;
    }

    // 清除按鈕 - 當有選擇檔案或已上傳內容時啟用
    if (elements.clearFilesBtn) {
        elements.clearFilesBtn.disabled = !(hasFile || hasExtractedText || hasHeaderJson || hasMarkdownTable);
    }

    // 分析規格按鈕 - 當有提取的文字且不在規格分析模式下時顯示
    if (elements.generateMarkdownBtn) {
        elements.generateMarkdownBtn.style.display = hasExtractedText && !isSpecAnalysisVisible ? 'inline-flex' : 'none';
        elements.generateMarkdownBtn.disabled = !hasExtractedText;
    }

    // 校對規格按鈕 - 當在規格分析模式下且有輸入回饋時啟用
    if (elements.reviewSpecBtn) {
        elements.reviewSpecBtn.disabled = !(isSpecAnalysisVisible && hasFeedback);
    }

    // 確認並生成按鈕 - 當在規格分析模式下且有Header JSON和Markdown內容時啟用
    if (elements.confirmMarkdownBtn) {
        elements.confirmMarkdownBtn.disabled = !(isSpecAnalysisVisible && hasHeaderJson && hasMarkdownTable);
    }

    // 下載Header按鈕 - 當有Header JSON時啟用
    if (elements.downloadHeaderBtn) {
        elements.downloadHeaderBtn.disabled = !hasHeaderJson;
    }

    // 下載合成資料按鈕
    if (elements.downloadSyntheticBtn) {
        elements.downloadSyntheticBtn.disabled = !hasSyntheticDataContent;
    }

    // 校對合成資料按鈕的狀態
    if (elements.reviewSyntheticBtn) {
        elements.reviewSyntheticBtn.disabled = !(isSyntheticSectionVisible && hasSyntheticDataContent && hasSyntheticFeedback);
    }
}

async function generateSpecAnalysis(text, filename) {
    updateStatus('正在分析規格，提取範例與 Body...');
    elements.generateMarkdownBtn.disabled = true;

    try {
        const response = await fetch(`${API_BASE}/generate-markdown`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text, filename })
        });
        if (!response.ok) { throw new Error(`HTTP 錯誤: ${response.status}`); }

        const result = await response.json();
        console.log('後端回傳 (generate-markdown):', JSON.stringify(result, null, 2));

        const headerContent = result.data?.header_json;
        const markdownContent = result.data?.body_markdown;

        if (result.success) {
            let isValidHeader = false;
            if (headerContent && typeof headerContent === 'object') {
                // 判斷是否為有效的 Header 內容 (非 null、非空物件、非空陣列)
                if (Array.isArray(headerContent)) {
                    isValidHeader = headerContent.length > 0;
                } else { // 如果是物件
                    isValidHeader = Object.keys(headerContent).length > 0;
                }
            }

            if (isValidHeader) {
                // 1. 儲存原始的 JSON 物件/陣列
                headerJsonData = headerContent;

                // 2. 直接將 JSON 物件美化成字串，用於顯示
                const jsonString = JSON.stringify(headerContent, null, 2);
                headerEditor.setValue(jsonString);

                // 3. 將編輯器模式設定為 JSON，以獲得正確的語法高亮
                headerEditor.setOption('mode', { name: 'javascript', json: true });

            } else {
                // 處理所有無效情況：null, undefined, {}, []
                headerJsonData = null;
                headerEditor.setValue("// 未能從文件中提取有效的請求範例。");
                // 註解使用 Markdown 模式顯示更清晰
                headerEditor.setOption('mode', 'markdown');
            }

            // --- Body Markdown 的處理邏輯保持不變 ---
            if (markdownContent) {
                markdownTable = markdownContent;
                markdownEditor.setValue(markdownContent);
            } else {
                markdownTable = "| Field | Type | Length | Notes |\n|---|---|---|---|\n| (未能從文件中提取 Body 資訊) | | | |";
                markdownEditor.setValue(markdownTable);
            }

            updateStatus('規格分析完成，請檢視並確認');
            activateSection('spec');

        } else {
            throw new Error(result.error || '從後端回應中找不到有效的規格內容');
        }
    } catch (error) {
        console.error('規格分析錯誤:', error);
        updateStatus(`規格分析失敗: ${error.message}`);
        alert(`規格分析失敗: ${error.message}`);
    } finally {
        updateButtonStates();
    }
}

async function reviewMarkdownTable(markdown, userInput) {
    updateStatus('正在校對 Body 表格...');
    elements.reviewSpecBtn.disabled = true;

    try {
        const response = await fetch(`${API_BASE}/review-markdown`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ markdown, user_input: userInput })
        });
        if (!response.ok) throw new Error(`HTTP 錯誤: ${response.status}`);
        const result = await response.json();

        const newMarkdownContent = result.data?.data?.markdown;
        if (result.success && typeof newMarkdownContent === 'string') {
            markdownTable = newMarkdownContent;
            markdownEditor.setValue(newMarkdownContent);
            updateStatus('Body 表格校對完成');
        } else {
            throw new Error(result.error || '從後端回應中找不到有效的 Markdown 內容');
        }
    } catch (error) {
        console.error('校對 Markdown 表格錯誤:', error);
        updateStatus(`校對 Markdown 表格失敗: ${error.message}`);
        alert(`校對 Markdown 表格失敗: ${error.message}`);
    } finally {
        updateButtonStates();
    }
}

async function reviewSyntheticData(currentData, userInput) {
    updateStatus('正在校對合成資料...');
    elements.reviewSyntheticBtn.disabled = true;

    try {
        const response = await fetch(`${API_BASE}/review-synthetic-data`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                synthetic_data_markdown: currentData,
                user_input: userInput
            })
        });

        if (!response.ok) throw new Error(`HTTP 錯誤: ${response.status}`);
        const result = await response.json();

        if (result.success && result.data) {
            // 更新全域變數和編輯器
            syntheticData = result.data.synthetic_data_markdown;
            syntheticDataCsv = result.data.synthetic_data_csv;
            syntheticDataEditor.setValue(syntheticData);

            updateStatus('合成資料校對完成');
            showNotification('合成資料已更新', 'success');
        } else {
            throw new Error(result.error || '從後端回應中找不到有效的合成資料');
        }
    } catch (error) {
        console.error('校對合成資料錯誤:', error);
        updateStatus(`校對合成資料失敗: ${error.message}`);
        alert(`校對合成資料失敗: ${error.message}`);
    } finally {
        elements.reviewSyntheticBtn.disabled = false;
        updateButtonStates();
    }
}

function setUILoading(isLoading) {
    elements.confirmMarkdownBtn.disabled = isLoading;
    elements.reviewSpecBtn.disabled = isLoading;
}

async function generateSyntheticData(filename) {
    const numRowsStr = window.prompt("請輸入要生成的資料筆數：", "30");
    if (numRowsStr === null) {
        updateStatus('已取消生成合成資料');
        return;
    }

    const numRows = parseInt(numRowsStr, 10);
    if (isNaN(numRows) || numRows <= 0) {
        alert("請輸入一個大於 0 的有效數字。");
        updateStatus('輸入無效，已取消生成');
        return;
    }

    updateStatus(`正在啟動生成 ${numRows} 筆資料的任務...`);
    setUILoading(true);

    try {
        // 1. 從編輯器獲取原始字串
        const headerJsonString = headerEditor.getValue();
        const finalBodyMarkdown = markdownEditor.getValue();

        // 2. 【核心修正】驗證字串是否為有效的 JSON，但不改變要發送的變數
        try {
            JSON.parse(headerJsonString); // 僅用於驗證，如果格式錯誤會拋出異常
        } catch (e) {
            // 如果解析失敗，提示使用者並中止
            alert('「API Body (JSON 規格)」中的內容不是有效的 JSON 格式，請修正後再試。\n\n錯誤詳情: ' + e.message);
            updateStatus('生成中止：Header JSON 格式錯誤。');
            setUILoading(false);
            return;
        }

        if (!extractedText) {
            throw new Error("找不到原始文件的提取文字，無法繼續生成。請重新上傳檔案。");
        }

        // 3. 【核心修正】發送請求時，傳遞原始的 `headerJsonString` 字串
        const response = await fetch(`${API_BASE}/start-synthetic-data-task`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                body_markdown: finalBodyMarkdown,
                header_json_markdown: headerJsonString, // <-- 傳送原始字串，而不是解析後的物件
                full_doc_text: extractedText,
                filename: filename,
                num_rows: numRows
            })
        });

        if (!response.ok) {
             const errorText = await response.text();
             // 嘗試解析錯誤文字，如果它是 JSON，則顯示更友善的訊息
             try {
                const errorJson = JSON.parse(errorText);
                // 檢查是否有 FastAPI 的詳細錯誤結構
                const detail = errorJson.detail && Array.isArray(errorJson.detail) ? JSON.stringify(errorJson.detail) : errorJson.error;
                throw new Error(detail || `啟動任務失敗 (HTTP ${response.status})`);
             } catch (e) {
                // 如果解析失敗，或不是預期的錯誤結構，則顯示原始錯誤文字
                throw new Error(e.message.includes("啟動任務失敗") ? e.message : `啟動任務失敗 (HTTP ${response.status}): ${errorText}`);
             }
        }

        const startResult = await response.json();
        const taskId = startResult.data?.task_id;

        if (!startResult.success || !taskId) {
            throw new Error(startResult.error || '啟動生成任務失敗，未收到 task_id');
        }

        updateStatus('任務已啟動，生成中...請稍候。');
        pollForTaskResult(taskId);

    } catch (error) {
        console.error('生成失敗:', error);
        updateStatus(`生成合成資料失敗: ${error.message}`);
        alert(`生成合成資料失敗: ${error.message}`);
        setUILoading(false);
    }
}

function pollForTaskResult(taskId, interval = 3000, maxAttempts = 100) {
    let attempts = 0;
    const intervalId = setInterval(async () => {
        if (attempts >= maxAttempts) {
            clearInterval(intervalId);
            updateStatus('生成超時，請稍後再試或檢查後端日誌。');
            alert('生成超時，請稍後再試。');
            setUILoading(false);
            return;
        }

        try {
            const statusResponse = await fetch(`${API_BASE}/get-task-status/${taskId}`);
            const task = await statusResponse.json();

            if (task.status === 'complete') {
                clearInterval(intervalId);
                updateStatus('生成成功！請檢視或進行校對。');
                const resultData = task.result?.data;
                if (resultData) {
                    syntheticData = resultData.synthetic_data_markdown || '';
                    syntheticDataCsv = resultData.synthetic_data_csv || '';
                    syntheticDataEditor.setValue(syntheticData);
                } else {
                    syntheticData = "無法解析結果";
                    syntheticDataCsv = "";
                }
                syntheticDataEditor.setOption("readOnly", false);
                activateSection('synthetic');
                setUILoading(false);
                updateButtonStates();
            } else if (task.status === 'error') {
                clearInterval(intervalId);
                const errorMessage = task.error || '未知錯誤';
                updateStatus(`生成失敗: ${errorMessage}`);
                alert(`生成失敗: ${errorMessage}`);
                setUILoading(false);
            } else {
                attempts++;
                updateStatus(`生成中... (第 ${attempts} 次檢查)`);
            }
        } catch (error) {
            attempts++;
            console.error('輪詢狀態時出錯:', error);
            updateStatus(`輪詢狀態時出錯: ${error.message}`);
        }
    }, interval);
}

function downloadTextAsFile(text, filename, mimeType = 'text/plain') {
    const blob = new Blob([text], { type: mimeType });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.URL.revokeObjectURL(url);
}

function updateStatus(message) {
    console.log('更新狀態:', message);
    const statusElement = document.getElementById('status');
    if (statusElement) {
        statusElement.textContent = message;
    }
}

function showNotification(message, type = 'info') {
    console.log(`顯示通知 [${type}]:`, message);
    const notification = document.getElementById('notification');
    const notificationMessage = document.querySelector('.notification-message');
    const notificationIcon = document.querySelector('.notification-icon');

    if (!notification || !notificationMessage || !notificationIcon) {
        console.error('找不到通知元素');
        return;
    }

    // 設置訊息和圖標
    notificationMessage.textContent = message;
    notification.className = `notification-toast ${type}`;

    // 設置圖標
    const icons = {
        'success': '✓',
        'error': '✗',
        'warning': '!',
        'info': 'i'
    };
    notificationIcon.textContent = icons[type] || icons['info'];

    // 顯示通知
    notification.style.display = 'flex';

    // 5秒後自動隱藏
    setTimeout(() => {
        notification.style.display = 'none';
    }, 5000);
}

function hideNotification() {
    const notification = document.getElementById('notification');
    if (notification) {
        notification.style.display = 'none';
    }
}

function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}