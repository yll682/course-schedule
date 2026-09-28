const semesterSelect = document.getElementById('semester');
const refreshButton = document.getElementById('refresh');
const statusText = document.getElementById('status');
const results = document.getElementById('results');
const summary = document.getElementById('summary');
const tableWrap = document.getElementById('table-wrap');
const gradesBody = document.getElementById('grades-body');
const loginHelp = document.getElementById('login-help');
const pageTitle = document.getElementById('page-title');
const backLink = document.getElementById('back-link');
const adminViewBanner = document.getElementById('admin-view');
const bestOnlyToggle = document.getElementById('best-only');
const targetUser = new URLSearchParams(location.search).get('as');
const adminView = Boolean(targetUser);
const gradeApi = adminView
    ? `/api/admin/grades/${encodeURIComponent(targetUser)}`
    : '/api/grades';
let semestersLoaded = false;
let currentGradeData = null;
let currentGradePayload = null;
let csrfToken = null;
const qualitativeGradeRanks = new Map([
    ['优秀', 90], ['良好', 80], ['中等', 70],
    ['及格', 60], ['合格', 60], ['通过', 60],
    ['不及格', 0], ['不合格', 0], ['未通过', 0],
]);
const failingGradeLabels = new Set(['不及格', '不合格', '未通过']);

if (adminView) {
    backLink.href = '/admin.html';
    backLink.textContent = '← 设置';
    refreshButton.hidden = true;
}

async function getCsrfToken() {
    if (csrfToken) return csrfToken;
    const response = await fetch('/api/csrf-token', { credentials: 'include', cache: 'no-store' });
    if (!response.ok) throw new Error('无法获取安全令牌，请刷新页面重试');
    const payload = await response.json();
    csrfToken = payload.csrf_token;
    return csrfToken;
}

async function requestGrades(url, options = {}) {
    const method = (options.method || 'GET').toUpperCase();
    const headers = new Headers(options.headers || {});
    if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
        headers.set('X-CSRF-Token', await getCsrfToken());
        headers.set('Content-Type', 'application/json');
    }
    const response = await fetch(url, {
        ...options,
        method,
        headers,
        credentials: 'include',
        cache: 'no-store',
    });
    if (response.status === 401) {
        loginHelp.hidden = false;
        throw new Error('请登录后查询成绩');
    }
    if (!response.headers.get('content-type')?.includes('application/json')) {
        throw new Error('成绩查询失败，请稍后刷新重试');
    }
    const payload = await response.json();
    if (!response.ok || !payload.success) {
        loginHelp.hidden = response.status === 403;
        throw new Error(payload.message || '成绩查询失败，请稍后刷新重试');
    }
    return payload;
}

function setLoading(loading) {
    semesterSelect.disabled = loading || !semestersLoaded;
    refreshButton.disabled = loading;
    bestOnlyToggle.disabled = loading || !currentGradeData;
    results.setAttribute('aria-busy', String(loading));
}

function clearGrades() {
    gradesBody.replaceChildren();
    summary.replaceChildren();
    summary.hidden = true;
    tableWrap.hidden = true;
    loginHelp.hidden = true;
    currentGradeData = null;
    currentGradePayload = null;
}

function isFailingGrade(value) {
    if (value === '' || value === null || value === undefined) return false;
    const normalizedValue = String(value).trim();
    if (failingGradeLabels.has(normalizedValue)) return true;
    const numericValue = Number(normalizedValue);
    return Number.isFinite(numericValue) && numericValue < 60;
}

function formatCacheTime(value) {
    if (!value) return '';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('zh-CN');
}

function gradeRank(value) {
    if (value !== '' && value !== null && value !== undefined) {
        const numericValue = Number(value);
        if (Number.isFinite(numericValue)) return numericValue;
    }
    return qualitativeGradeRanks.get(String(value).trim()) ?? Number.NEGATIVE_INFINITY;
}

function highestGrades(achievements) {
    const bestByCourse = new Map();
    for (const grade of achievements) {
        const courseKey = grade.kcbh || grade.courseName || grade.cj0708id;
        const current = bestByCourse.get(courseKey);
        if (!current || gradeRank(grade.fraction) > gradeRank(current.fraction)) {
            bestByCourse.set(courseKey, grade);
        }
    }
    return Array.from(bestByCourse.values());
}

function renderGrades(data, payload) {
    gradesBody.replaceChildren();
    summary.replaceChildren();
    const allAchievements = data.achievement;
    const achievements = bestOnlyToggle.checked ? highestGrades(allAchievements) : allAchievements;
    const courseKeys = allAchievements.map(grade => grade.kcbh || grade.courseName);
    const courseCount = new Set(courseKeys).size;
    const cacheTime = formatCacheTime(payload.cache_time);
    const cacheStatus = payload.from_cache && cacheTime ? ` · 缓存于 ${cacheTime}` : '';
    const refreshStatus = payload.refreshing ? ' · 正在后台刷新' : '';
    const recordStatus = bestOnlyToggle.checked
        ? `显示 ${achievements.length} 条最高成绩 · 全部 ${allAchievements.length} 条记录`
        : `共 ${allAchievements.length} 条成绩记录 · ${courseCount} 门课程`;
    statusText.textContent = achievements.length
        ? `${recordStatus}${cacheStatus}${refreshStatus}`
        : `该学期暂无已发布成绩${cacheStatus}${refreshStatus}`;
    for (const [label, value] of [
        ['平均成绩', data.pjcj], ['平均学分绩点', data.pjxfjd],
        ['已修总学分', data.yxzxf], ['总学分绩点', data.zxfjd],
    ]) {
        if (value === '' || value === null || value === undefined) continue;
        const item = document.createElement('span');
        item.textContent = `${label}：${value}`;
        summary.append(item);
    }
    summary.hidden = !summary.childElementCount;
    document.getElementById('grades-caption').textContent = semesterSelect.selectedOptions[0].textContent;
    for (const grade of achievements) {
        const row = document.createElement('tr');
        row.dataset.gradeId = grade.cj0708id || '';
        for (const key of ['courseName', 'fraction', 'credit', 'curriculumAttributes', 'courseNature', 'examinationNature']) {
            const cell = document.createElement('td');
            cell.textContent = grade[key] === '' || grade[key] == null ? '—' : String(grade[key]);
            if (key === 'courseName' && grade.kcbh) {
                const code = document.createElement('span');
                code.className = 'course-code';
                code.textContent = grade.kcbh;
                cell.append(code);
            }
            if (key === 'fraction' && isFailingGrade(grade[key])) {
                cell.classList.add('failing-grade');
            }
            row.append(cell);
        }
        gradesBody.append(row);
    }
    tableWrap.hidden = !achievements.length;
}

async function loadGrades(force = false) {
    clearGrades();
    setLoading(true);
    statusText.textContent = '正在查询成绩…';
    try {
        const payload = force && !adminView
            ? await requestGrades('/api/grades/refresh', {
                method: 'POST',
                body: JSON.stringify({ semester: semesterSelect.value }),
            })
            : await requestGrades(`${gradeApi}?semester=${encodeURIComponent(semesterSelect.value)}`);
        if (payload.record_scope !== 'all') {
            throw new Error('服务器成绩缓存格式已更新，请重启服务后刷新页面');
        }
        currentGradeData = payload.data;
        currentGradePayload = payload;
        renderGrades(payload.data, payload);
    } catch (error) {
        statusText.textContent = error instanceof TypeError ? '无法连接服务器，请检查网络后重试' : error.message;
    } finally {
        setLoading(false);
    }
}

async function loadSemesters(force = false) {
    clearGrades();
    setLoading(true);
    statusText.textContent = '正在获取学期…';
    try {
        const payload = force && !adminView
            ? await requestGrades('/api/grades/semesters/refresh', { method: 'POST', body: '{}' })
            : await requestGrades(`${gradeApi}/semesters`);
        if (adminView) {
            const targetName = payload.target_name || targetUser;
            pageTitle.textContent = `💯 ${targetName}的成绩`;
            adminViewBanner.textContent = `管理员查看 · ${targetName}（${payload.target_user || targetUser}）· 仅显示服务器缓存`;
            adminViewBanner.hidden = false;
        }
        semesterSelect.replaceChildren();
        for (const semester of payload.semesters) {
            const option = document.createElement('option');
            option.value = semester.semesterId;
            option.textContent = semester.semesterName;
            option.selected = semester.semesterId === payload.current_semester;
            semesterSelect.append(option);
        }
        semestersLoaded = payload.semesters.length > 0;
        if (!semestersLoaded) {
            statusText.textContent = '暂无可查询的学期';
            return;
        }
        if (!payload.semesters.some(item => item.semesterId === payload.current_semester)) {
            semesterSelect.insertBefore(new Option('请选择学期', '', true, true), semesterSelect.firstChild);
            semesterSelect.firstChild.disabled = true;
            statusText.textContent = '请选择需要查询的学期';
            return;
        }
        await loadGrades();
    } catch (error) {
        statusText.textContent = error instanceof TypeError ? '无法连接服务器，请检查网络后重试' : error.message;
    } finally {
        setLoading(false);
    }
}

semesterSelect.addEventListener('change', () => loadGrades());
bestOnlyToggle.addEventListener('change', () => {
    if (currentGradeData && currentGradePayload) renderGrades(currentGradeData, currentGradePayload);
});
refreshButton.addEventListener('click', () => semestersLoaded && semesterSelect.value ? loadGrades(true) : loadSemesters(true));
window.addEventListener('pagehide', clearGrades);
window.addEventListener('pageshow', event => { if (event.persisted) loadSemesters(); });
loadSemesters();
