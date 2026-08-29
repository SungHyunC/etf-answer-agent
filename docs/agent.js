/*!
 * ETF Answer Agent — 브라우저용 순수 JavaScript 포팅 (rule 백엔드)
 *
 * src/ 의 파이썬 파이프라인을 외부 의존성 없이 재현한다.
 *   ① preprocess  오타 교정 · 엔티티 식별 · 수준 판별        (src/nodes/preprocess.py)
 *   ② classify    규제 신호어 최우선 규칙 분류               (src/nodes/classify.py)
 *   ③ retrieve    ROUTE 창고 라우팅 + TF-IDF 검색            (src/nodes/retrieve.py)
 *   ④ generate    템플릿 답변 / REFUSAL / NO_EVIDENCE        (src/nodes/generate.py)
 *   ⑤ compliance  C-01~C-08 검증 게이트 + 반려 루프           (src/nodes/compliance.py, src/graph.py)
 *
 * 검색기는 scikit-learn 의
 *   TfidfVectorizer(analyzer="char_wb", ngram_range=(2,4),
 *                   sublinear_tf=False, smooth_idf=True, norm="l2")
 * 와 cosine_similarity 를 그대로 재현한다.
 *
 * 데이터·규칙은 docs/data.json(= tools/export_data.py 가 src/ 에서 내보낸 것)에서 주입받는다.
 * 이 파일은 fetch 를 하지 않는다 — 호출 측이 파싱한 객체를 init(data) 로 넘긴다.
 *
 *   ETFAgent.init(data);
 *   var r = ETFAgent.ask("ETF가 뭔가요?");
 *
 * ES5+ 문법과 브라우저 내장 기능만 사용한다.
 */
(function (root) {
  'use strict';

  /* ────────────────────────────────────────────────────────────────────
   * 0. 파이썬 내장 동작 에뮬레이션
   * ──────────────────────────────────────────────────────────────── */

  /** Python str.strip(chars) — 앞뒤에서 chars 집합의 문자를 제거. */
  function pyStrip(s, chars) {
    if (s === null || s === undefined) return '';
    s = String(s);
    if (chars === undefined) return s.replace(/^\s+|\s+$/g, '');
    var start = 0;
    var end = s.length;
    while (start < end && chars.indexOf(s.charAt(start)) !== -1) start++;
    while (end > start && chars.indexOf(s.charAt(end - 1)) !== -1) end--;
    return s.slice(start, end);
  }

  /** Python str.splitlines() 근사. */
  function splitLines(s) {
    return String(s).split(/\r\n|\r|\n/);
  }

  /** Python str.replace(a, b) — 리터럴 전체 치환. */
  function replaceAll(s, from, to) {
    if (from === '') return String(s);
    return String(s).split(from).join(to);
  }

  /** Python repr(list[str]) — trace 문자열의 엔티티 표기를 그대로 맞춘다. */
  function pyReprList(arr) {
    var parts = [];
    for (var i = 0; i < arr.length; i++) {
      var s = String(arr[i]).replace(/\\/g, '\\\\');
      parts.push(s.indexOf("'") !== -1 ? '"' + s + '"' : "'" + s + "'");
    }
    return '[' + parts.join(', ') + ']';
  }

  /** Python f"{x:.2f}". */
  function fmt2(x) {
    return Number(x).toFixed(2);
  }

  /** Python round(x, 4) 근사 — 실수 동률(정확히 …5)은 사실상 발생하지 않는다. */
  function round4(x) {
    return Number(Number(x).toFixed(4));
  }

  /** 코드포인트 배열 — 서로게이트 쌍에서도 파이썬과 같은 길이/슬라이스를 갖게 한다. */
  function codePoints(s) {
    var out = [];
    for (var i = 0; i < s.length; i++) {
      var c = s.charCodeAt(i);
      if (c >= 0xd800 && c <= 0xdbff && i + 1 < s.length) {
        var d = s.charCodeAt(i + 1);
        if (d >= 0xdc00 && d <= 0xdfff) {
          out.push(s.charAt(i) + s.charAt(i + 1));
          i++;
          continue;
        }
      }
      out.push(s.charAt(i));
    }
    return out;
  }

  function includesAny(text, list) {
    for (var i = 0; i < list.length; i++) {
      if (text.indexOf(list[i]) !== -1) return true;
    }
    return false;
  }

  function hasOwn(o, k) {
    return o !== null && o !== undefined && Object.prototype.hasOwnProperty.call(o, k);
  }

  function isArray(v) {
    return Object.prototype.toString.call(v) === '[object Array]';
  }

  /** 여러 후보 키 중 먼저 존재하는 값 (data.json 스키마 편차 흡수). */
  function pick(obj, keys, fallback) {
    if (!obj) return fallback;
    for (var i = 0; i < keys.length; i++) {
      if (hasOwn(obj, keys[i]) && obj[keys[i]] !== null && obj[keys[i]] !== undefined) {
        return obj[keys[i]];
      }
    }
    return fallback;
  }

  /* ────────────────────────────────────────────────────────────────────
   * 1. TF-IDF — sklearn TfidfVectorizer(analyzer="char_wb", ngram_range=(2,4))
   *
   *   preprocess : lowercase=True, strip_accents=None
   *   analyzer   : _white_spaces(/\s\s+/) → " " 치환 후 split(),
   *                각 단어를 앞뒤 공백으로 패딩(" 단어 ")하고 그 안에서만 n-gram 추출.
   *                n 마다 w[0:n] 을 먼저 넣고 한 칸씩 밀며, 단어가 n 보다 짧으면
   *                (offset === 0) 그 단어는 한 번만 세고 더 큰 n 은 건너뛴다.
   *   idf        : smooth_idf=True → ln((1 + n_docs) / (1 + df)) + 1
   *   tf         : sublinear_tf=False → 원 빈도 그대로
   *   norm       : l2
   *   유사도     : L2 정규화 벡터의 내적 (= cosine_similarity)
   * ──────────────────────────────────────────────────────────────── */

  var WHITE_SPACES = /\s\s+/g;

  function charWbNgrams(text, minN, maxN) {
    var doc = String(text).replace(WHITE_SPACES, ' ');
    var words = doc.split(/\s+/);
    var ngrams = [];
    for (var wi = 0; wi < words.length; wi++) {
      if (words[wi] === '') continue; // Python str.split() 은 빈 토큰을 만들지 않는다
      var w = codePoints(' ' + words[wi] + ' ');
      var wLen = w.length;
      for (var n = minN; n <= maxN; n++) {
        var offset = 0;
        ngrams.push(w.slice(0, n).join(''));
        while (offset + n < wLen) {
          offset += 1;
          ngrams.push(w.slice(offset, offset + n).join(''));
        }
        if (offset === 0) break; // 짧은 단어는 한 번만 센다
      }
    }
    return ngrams;
  }

  function countNgrams(text, minN, maxN) {
    var grams = charWbNgrams(String(text).toLowerCase(), minN, maxN);
    var counts = {};
    for (var i = 0; i < grams.length; i++) {
      var g = grams[i];
      counts[g] = (counts[g] || 0) + 1;
    }
    return counts;
  }

  /**
   * 문서 집합 하나에 대한 TF-IDF 색인.
   * 부동소수 합산 순서를 sklearn(CSR = 어휘 인덱스 오름차순)과 맞추기 위해
   * 각 벡터의 항을 어휘 인덱스순으로 정렬해 노름과 내적을 계산한다.
   */
  function TfidfIndex(corpus, minN, maxN) {
    this.minN = minN;
    this.maxN = maxN;

    var nDocs = corpus.length;
    var i, j, term;

    var rawCounts = [];
    var df = {};
    for (i = 0; i < nDocs; i++) {
      var counts = countNgrams(corpus[i], minN, maxN);
      rawCounts.push(counts);
      for (term in counts) {
        if (!hasOwn(counts, term)) continue;
        df[term] = (df[term] || 0) + 1;
      }
    }

    // sklearn 은 어휘를 사전순으로 정렬해 인덱스를 부여한다.
    var vocab = [];
    for (term in df) {
      if (hasOwn(df, term)) vocab.push(term);
    }
    vocab.sort(function (a, b) {
      return a < b ? -1 : a > b ? 1 : 0;
    });

    this.index = {}; // term -> 어휘 인덱스
    this.idf = {};   // term -> idf
    for (j = 0; j < vocab.length; j++) {
      term = vocab[j];
      this.index[term] = j;
      this.idf[term] = Math.log((nDocs + 1) / (df[term] + 1)) + 1;
    }

    // 문서 벡터: tf * idf → L2 정규화
    this.rows = [];
    for (i = 0; i < nDocs; i++) this.rows.push(this._vectorize(rawCounts[i]));
  }

  /** 카운트 맵 → { terms: [어휘 인덱스순], weights: {term: w} } (L2 정규화 완료) */
  TfidfIndex.prototype._vectorize = function (counts) {
    var self = this;
    var terms = [];
    var term;
    for (term in counts) {
      // 어휘에 없는 n-gram 은 버린다 (CountVectorizer.transform 과 동일)
      if (hasOwn(counts, term) && hasOwn(this.idf, term)) terms.push(term);
    }
    terms.sort(function (a, b) {
      return self.index[a] - self.index[b];
    });

    var weights = {};
    var sq = 0.0;
    var i, w;
    for (i = 0; i < terms.length; i++) {
      w = counts[terms[i]] * this.idf[terms[i]];
      weights[terms[i]] = w;
      sq += w * w;
    }
    var norm = Math.sqrt(sq);
    if (norm > 0) {
      for (i = 0; i < terms.length; i++) weights[terms[i]] = weights[terms[i]] / norm;
    }
    return { terms: terms, weights: weights };
  };

  TfidfIndex.prototype.transform = function (query) {
    return this._vectorize(countNgrams(query, this.minN, this.maxN));
  };

  /** 정규화된 두 희소 벡터의 코사인 유사도(= 내적). */
  TfidfIndex.prototype.similarities = function (qvec) {
    var out = [];
    for (var i = 0; i < this.rows.length; i++) {
      var row = this.rows[i];
      var s = 0.0;
      for (var t = 0; t < qvec.terms.length; t++) {
        var term = qvec.terms[t];
        if (hasOwn(row.weights, term)) s += qvec.weights[term] * row.weights[term];
      }
      out.push(s);
    }
    return out;
  };

  /* ────────────────────────────────────────────────────────────────────
   * 2. Store — src/data/vectorstore.py
   * ──────────────────────────────────────────────────────────────── */

  function Store(name, label, docs) {
    this.name = name;
    this.label = label || name;
    this.docs = docs || [];
    var corpus = [];
    for (var i = 0; i < this.docs.length; i++) {
      var d = this.docs[i];
      corpus.push((d.title || '') + ' ' + (d.text || ''));
    }
    this.index = new TfidfIndex(corpus, 2, 4);
  }

  Store.prototype.search = function (query, k, minScore) {
    k = k === undefined ? 3 : k;
    minScore = minScore === undefined ? 0.0 : minScore;

    var scores = this.index.similarities(this.index.transform(query));
    var order = [];
    for (var i = 0; i < scores.length; i++) order.push(i);
    // Python: sorted(range(n), key=scores.__getitem__, reverse=True) — 동점은 원래 순서 유지
    order.sort(function (a, b) {
      if (scores[b] > scores[a]) return 1;
      if (scores[b] < scores[a]) return -1;
      return a - b;
    });

    var out = [];
    for (var r = 0; r < order.length && r < k; r++) {
      var idx = order[r];
      if (scores[idx] < minScore) continue; // k 로 자른 뒤에 거르는 순서까지 동일
      var src = this.docs[idx];
      var hit = {};
      for (var key in src) {
        if (hasOwn(src, key)) hit[key] = src[key];
      }
      hit.score = round4(scores[idx]);
      hit.store = this.label;
      out.push(hit);
    }
    return out;
  };

  /* ────────────────────────────────────────────────────────────────────
   * 3. 기본 상수 — data.json 에 값이 없을 때만 쓰이는 폴백 (src/ 원본과 1:1)
   * ──────────────────────────────────────────────────────────────── */

  var DEFAULTS = {};

  // src/nodes/preprocess.py — 파이썬 dict 삽입 순서를 배열로 보존
  DEFAULTS.TYPO_MAP = [
    ['ETF으', 'ETF은'], ['이티에프', 'ETF'], ['etf', 'ETF'],
    ['총보스', '총보수'], ['총 보수', '총보수'], ['총보수률', '총보수율'],
    ['분배근', '분배금'], ['분배 금', '분배금'],
    ['나스닥100지수', '나스닥100'], ['s&p', 'S&P'],
    ['리벨런싱', '리밸런싱'], ['리발란싱', '리밸런싱'], ['리밸랜싱', '리밸런싱'],
    ['괴리률', '괴리율'], ['추척오차', '추적오차'],
    ['얼마애요', '얼마예요'], ['머예요', '뭐예요'], ['먼가요', '뭔가요'],
    ['알려조', '알려줘'], ['알려주세여', '알려주세요']
  ];

  DEFAULTS.BEGINNER_HINTS = ['뭔가요', '뭐예요', '무엇인가요', '처음', '초보', '쉽게', '차이가 뭐', '어떻게 사', '설명해'];
  DEFAULTS.EXPERT_HINTS = ['괴리율', '추적오차', '리밸런싱', '정기변경', '실부담비용', '환헤지', 'NAV', 'LP', '듀레이션'];

  // src/nodes/classify.py — 배열 순서가 곧 우선순위(0번이 규제 경로)
  DEFAULTS.CLASSIFY_RULES = [
    ['out_of_scope', ['추천', '뭐 사', '뭘 사', '사는게', '사는 게', '사야 하나', '사도 될까',
      '살까', '팔까', '팔아야', '매수해', '매도해', '골라', '고르면',
      '좋을까', '좋을까요', '괜찮을까', '나을까', '유망', '유리한가',
      '얼마나 오를', '오를까', '떨어질까', '전망', '수익률 보장', '얼마 벌',
      '투자해도', '손실 안', '대신 사', '얼마 넣', '비중 얼마',
      '어디에 넣', '어디다 넣', '넣으면', '원금 보장', '보장되나', '보장 되나',
      '손해 안', '안전한가', '묻어두', '굴리면']],
    ['disclosure', ['분배금', '공시', '뉴스', '정기변경', '리밸런싱', '일정', '언제 지급', '기준일']],
    ['faq', ['어떻게 사', '어떻게 매수', '세금', '과세', '계좌', '수수료 환급', '환매',
      '거래시간', '입금', '상담원', '문의']],
    ['etf_info', ['총보수', '기초지수', '구성종목', '순자산', '상장일', '티커', '종목코드',
      '보수', '유형', '몇 퍼센트', '얼마']],
    ['general', ['뭔가요', '뭐예요', '무엇인가요', '차이', '설명', '개념', '괴리율', '추적오차',
      '환헤지', '상장폐지']]
  ];

  DEFAULTS.DEFINITION_HINTS = ['뭔가요', '뭐예요', '무엇인가요', '무엇인지', '뭐야', '차이', '설명해', '개념'];

  // src/nodes/retrieve.py
  DEFAULTS.ROUTE = {
    etf_info: ['product'],
    disclosure: ['disclosure', 'product'],
    faq: ['faq', 'product'],
    general: ['product', 'faq'],
    out_of_scope: []
  };

  // src/nodes/generate.py
  DEFAULTS.REFUSAL =
    '죄송합니다. 해당 내용은 제가 안내해 드릴 수 있는 범위를 벗어납니다.\n\n' +
    '저는 ETF 상품 정보와 거래 절차 안내를 도와드리는 챗봇으로, ' +
    '특정 종목 추천이나 투자 판단, 수익률 전망은 제공하지 않습니다.\n' +
    '투자 판단이 필요하신 경우 영업점 또는 투자권유자문인력과 상담해 주세요.';

  DEFAULTS.NO_EVIDENCE =
    '죄송합니다. 문의하신 내용은 제가 확인할 수 있는 자료에서 찾지 못했습니다.\n' +
    '질문을 조금 더 구체적으로 남겨주시거나, 영업시간 중 고객센터로 문의해 주세요.';

  // src/nodes/compliance.py — [규칙 ID, 정규식(문자열), 사유]
  DEFAULTS.COMPLIANCE_RULES = [
    ['C-01', '(추천\\s*(합니다|해\\s*드립|드립니다|드려요))', '종목 추천 표현'],
    ['C-02', '(매수|매도|투자)\\s*(하세요|하시길|추천|권해|권유|권장)', '매매 권유 표현'],
    ['C-03', '(수익(률)?|이익|원금)\\s*(이|을|은)?\\s*(보장|확실|무조건)', '수익 보장 표현'],
    ['C-04', '(오를\\s*것|상승할\\s*것|하락할\\s*것|떨어질\\s*것)\\s*(입니다|이에요|예요|같습니다)', '가격 전망 단정'],
    ['C-05', '(유망|전망이\\s*밝|기대됩니다|좋은\\s*기회|지금이\\s*적기)', '투자 유인 표현'],
    ['C-06', '(사시면|사시는\\s*것이|담으시면|비중을\\s*늘리)', '매수 유도 표현'],
    ['C-07', '(반드시|틀림없이|100%)\\s*(수익|오릅|이익)', '확정적 단정']
  ];

  DEFAULTS.SAFE_TERMS = ['투자권유자문인력', '투자권유대행인', '투자권유준칙'];

  DEFAULTS.SAFE_FALLBACK =
    '죄송합니다. 문의하신 내용에 대해 정확한 안내를 드리기 어렵습니다.\n' +
    '정확한 상담을 위해 영업시간 중 고객센터 또는 영업점으로 문의해 주세요.';

  DEFAULTS.STORE_LABEL = { product: '상품 지식', faq: 'FAQ / VOC', disclosure: '공시 · 뉴스' };

  // src/config.py — data.json 에는 실려 있지 않은 값
  DEFAULTS.MIN_SIMILARITY = 0.08;
  DEFAULTS.MAX_REGENERATE = 2;

  // 근거 없는 수치 주장 탐지 (src/nodes/compliance.py NUM_RE)
  var NUM_RE = /\d[\d,]*\.?\d*\s*(?:%|원|억|조|배)/g;

  var LABELS = ['etf_info', 'disclosure', 'faq', 'general', 'out_of_scope'];

  /* ────────────────────────────────────────────────────────────────────
   * 4. 주입 데이터 (docs/data.json)
   * ──────────────────────────────────────────────────────────────── */

  var DB = {
    ready: false,
    etfNames: [],
    etfs: {},
    aliasKeys: [],
    aliases: {},
    stores: {},
    storeOrder: [],
    typoMap: DEFAULTS.TYPO_MAP,
    beginnerHints: DEFAULTS.BEGINNER_HINTS,
    expertHints: DEFAULTS.EXPERT_HINTS,
    classifyRules: DEFAULTS.CLASSIFY_RULES,
    definitionHints: DEFAULTS.DEFINITION_HINTS,
    route: DEFAULTS.ROUTE,
    complianceRules: [],
    safeTerms: DEFAULTS.SAFE_TERMS,
    refusal: DEFAULTS.REFUSAL,
    noEvidence: DEFAULTS.NO_EVIDENCE,
    safeFallback: DEFAULTS.SAFE_FALLBACK,
    config: { MIN_SIMILARITY: DEFAULTS.MIN_SIMILARITY, MAX_REGENERATE: DEFAULTS.MAX_REGENERATE }
  };

  function normalizeEtfRecord(raw) {
    if (typeof raw === 'string') return { record: raw, holdings: [] };
    var holdings = pick(raw, ['구성종목', 'holdings', 'top_holdings', 'constituents'], []);
    if (typeof holdings === 'string') holdings = holdings.split(/\s*,\s*/);
    if (!isArray(holdings)) holdings = [];
    return {
      ticker: String(pick(raw, ['ticker', '티커', '종목코드', 'code'], '')),
      baseIndex: String(pick(raw, ['기초지수', 'base_index', 'index', 'benchmark'], '')),
      fee: String(pick(raw, ['총보수', 'fee', 'total_fee', 'expense_ratio'], '')),
      type: String(pick(raw, ['유형', 'type', 'category'], '')),
      aum: String(pick(raw, ['순자산', 'aum', 'net_assets'], '')),
      dist: String(pick(raw, ['분배금주기', '분배금 주기', 'distribution', 'distribution_cycle'], '')),
      listed: String(pick(raw, ['상장일', 'listed', 'listing_date'], '')),
      holdings: holdings.slice(0),
      record: pick(raw, ['record', 'formatted'], null)
    };
  }

  function toDocList(list) {
    var out = [];
    if (!isArray(list)) return out;
    for (var i = 0; i < list.length; i++) {
      var d = list[i] || {};
      out.push({
        id: pick(d, ['id', 'doc_id'], ''),
        source: pick(d, ['source', '출처'], ''),
        title: pick(d, ['title', '제목'], ''),
        text: pick(d, ['text', 'content', '본문'], '')
      });
    }
    return out;
  }

  /** typo_map: dict(파이썬 삽입 순서 유지) 또는 [[wrong, right], ...] */
  function toPairList(v) {
    var pairs = [];
    var k;
    if (!v) return null;
    if (isArray(v)) {
      for (var i = 0; i < v.length; i++) {
        if (isArray(v[i]) && v[i].length >= 2) pairs.push([v[i][0], v[i][1]]);
      }
    } else {
      for (k in v) {
        if (hasOwn(v, k)) pairs.push([k, v[k]]);
      }
    }
    return pairs.length ? pairs : null;
  }

  function compileComplianceRules(rules) {
    var out = [];
    for (var i = 0; i < rules.length; i++) {
      var rid = rules[i][0];
      var pattern = rules[i][1];
      var reason = rules[i][2];
      var re;
      try {
        re = pattern instanceof RegExp ? pattern : new RegExp(pattern);
      } catch (e) {
        // 컴플라이언스 규칙이 조용히 사라지면 안 된다 — 초기화 단계에서 실패시킨다.
        throw new Error('ETFAgent.init: 컴플라이언스 규칙 ' + rid + ' 정규식 컴파일 실패 — ' + pattern);
      }
      out.push([rid, re, reason]);
    }
    return out;
  }

  /** docs/data.json 을 받아 초기화한다. */
  function init(data) {
    if (typeof data === 'string') data = JSON.parse(data);
    if (!data || typeof data !== 'object') {
      throw new Error('ETFAgent.init: data 객체가 필요합니다 (docs/data.json).');
    }

    var i, name, key;

    /* 1) 정형 ETF DB — 삽입 순서가 엔티티 탐색 순서다. */
    var etfs = pick(data, ['etfs', 'ETFS', 'etf_db', 'products'], {});
    DB.etfNames = [];
    DB.etfs = {};
    if (isArray(etfs)) {
      for (i = 0; i < etfs.length; i++) {
        name = pick(etfs[i], ['name', '상품명', 'title'], '');
        if (!name) continue;
        DB.etfNames.push(name);
        DB.etfs[name] = normalizeEtfRecord(etfs[i]);
      }
    } else {
      for (name in etfs) {
        if (!hasOwn(etfs, name)) continue;
        DB.etfNames.push(name);
        DB.etfs[name] = normalizeEtfRecord(etfs[name]);
      }
    }

    /* 2) 별칭 사전 */
    var aliases = pick(data, ['aliases', 'ALIASES'], {});
    DB.aliasKeys = [];
    DB.aliases = {};
    for (key in aliases) {
      if (!hasOwn(aliases, key)) continue;
      DB.aliasKeys.push(key);
      DB.aliases[key] = aliases[key];
    }

    /* 3) 문서 창고 — data.json 은 product_docs / faq_docs / disclosure_docs 로 내보낸다.
     *    stores:{} 형태로 들어오는 경우도 함께 받는다. */
    var labels = pick(data, ['store_label', 'store_labels', 'STORE_LABEL', 'labels'], {}) || {};
    var stores = pick(data, ['stores', 'STORES'], null);
    DB.stores = {};
    DB.storeOrder = [];

    function addStore(sname, label, docs) {
      DB.storeOrder.push(sname);
      DB.stores[sname] = new Store(sname, label || labels[sname] || DEFAULTS.STORE_LABEL[sname] || sname, docs);
    }

    if (stores && isArray(stores)) {
      for (i = 0; i < stores.length; i++) {
        var entry = stores[i] || {};
        var sn = pick(entry, ['name', 'id', 'key'], '');
        if (!sn) continue;
        addStore(sn, pick(entry, ['label'], null), toDocList(pick(entry, ['docs', 'documents', 'items'], [])));
      }
    } else if (stores) {
      for (key in stores) {
        if (hasOwn(stores, key)) addStore(key, null, toDocList(stores[key]));
      }
    } else {
      // "<창고이름>_docs" 키를 파일에 나온 순서대로 수집
      for (key in data) {
        if (!hasOwn(data, key)) continue;
        var m = /^(.+)_docs$/.exec(key);
        if (m && isArray(data[key])) addStore(m[1], null, toDocList(data[key]));
      }
    }
    if (!DB.storeOrder.length) {
      throw new Error('ETFAgent.init: 문서 창고를 찾지 못했습니다 (product_docs / faq_docs / disclosure_docs).');
    }

    /* 4) 규칙·문구 — data.json 이 단일 진실 공급원, 없으면 내장 폴백 */
    DB.typoMap = toPairList(pick(data, ['typo_map', 'TYPO_MAP'], null)) || DEFAULTS.TYPO_MAP;

    var bh = pick(data, ['beginner_hints', 'BEGINNER_HINTS'], null);
    DB.beginnerHints = isArray(bh) && bh.length ? bh : DEFAULTS.BEGINNER_HINTS;
    var eh = pick(data, ['expert_hints', 'EXPERT_HINTS'], null);
    DB.expertHints = isArray(eh) && eh.length ? eh : DEFAULTS.EXPERT_HINTS;

    var cr = pick(data, ['classify_rules', 'CLASSIFY_RULES'], null);
    DB.classifyRules = isArray(cr) && cr.length ? cr : DEFAULTS.CLASSIFY_RULES;
    var dh = pick(data, ['definition_hints', 'DEFINITION_HINTS'], null);
    DB.definitionHints = isArray(dh) && dh.length ? dh : DEFAULTS.DEFINITION_HINTS;

    var rt = pick(data, ['route', 'ROUTE'], null);
    DB.route = rt && !isArray(rt) ? rt : DEFAULTS.ROUTE;

    var comp = pick(data, ['compliance_rules', 'COMPLIANCE_RULES'], null);
    DB.complianceRules = compileComplianceRules(isArray(comp) && comp.length ? comp : DEFAULTS.COMPLIANCE_RULES);

    var st = pick(data, ['safe_terms', 'SAFE_TERMS'], null);
    DB.safeTerms = isArray(st) ? st : DEFAULTS.SAFE_TERMS;

    DB.refusal = String(pick(data, ['refusal', 'REFUSAL'], DEFAULTS.REFUSAL));
    DB.noEvidence = String(pick(data, ['no_evidence', 'NO_EVIDENCE'], DEFAULTS.NO_EVIDENCE));
    DB.safeFallback = String(pick(data, ['safe_fallback', 'SAFE_FALLBACK'], DEFAULTS.SAFE_FALLBACK));

    var cfg = pick(data, ['config', 'CONFIG'], {}) || {};
    DB.config = {
      MIN_SIMILARITY: Number(pick(cfg, ['min_similarity', 'MIN_SIMILARITY'], DEFAULTS.MIN_SIMILARITY)),
      MAX_REGENERATE: Number(pick(cfg, ['max_regenerate', 'MAX_REGENERATE'], DEFAULTS.MAX_REGENERATE))
    };

    DB.ready = true;
    return api;
  }

  function requireReady() {
    if (!DB.ready) throw new Error('ETFAgent: init(data) 를 먼저 호출해 주세요.');
  }

  /* ────────────────────────────────────────────────────────────────────
   * 5. 정형 DB 조회 — src/data/etf_db.py
   * ──────────────────────────────────────────────────────────────── */

  function resolveEntities(text) {
    var low = replaceAll(String(text).toLowerCase(), ' ', '');
    var hits = [];
    var i, name;
    for (i = 0; i < DB.etfNames.length; i++) {
      name = DB.etfNames[i];
      if (low.indexOf(replaceAll(name.toLowerCase(), ' ', '')) !== -1) hits.push(name);
    }
    for (i = 0; i < DB.aliasKeys.length; i++) {
      var alias = DB.aliasKeys[i];
      name = DB.aliases[alias];
      if (low.indexOf(alias) !== -1 && hits.indexOf(name) === -1) hits.push(name);
    }
    return hits;
  }

  function formatRecord(name) {
    var r = DB.etfs[name];
    if (!r) return '[' + name + ']';
    if (r.record) return r.record;
    return (
      '[' + name + ' (' + r.ticker + ')]\n' +
      '- 기초지수: ' + r.baseIndex + '\n' +
      '- 유형: ' + r.type + '\n' +
      '- 총보수: 연 ' + r.fee + '\n' +
      '- 순자산: ' + r.aum + '\n' +
      '- 분배금 주기: ' + r.dist + '\n' +
      '- 상장일: ' + r.listed + '\n' +
      '- 주요 구성종목: ' + r.holdings.join(', ')
    );
  }

  /* ────────────────────────────────────────────────────────────────────
   * 6. ① 발화 전처리 — src/nodes/preprocess.py
   * ──────────────────────────────────────────────────────────────── */

  function normalizeText(text) {
    var out = pyStrip(String(text));
    var fixes = [];
    for (var i = 0; i < DB.typoMap.length; i++) {
      var wrong = DB.typoMap[i][0];
      var right = DB.typoMap[i][1];
      if (out.indexOf(wrong) !== -1) {
        out = replaceAll(out, wrong, right);
        fixes.push([wrong, right]);
      }
    }
    out = out.replace(/\s+/g, ' ');
    return { text: out, fixes: fixes };
  }

  function detectLevel(text) {
    if (includesAny(text, DB.expertHints)) return 'expert';
    if (includesAny(text, DB.beginnerHints)) return 'beginner';
    return 'beginner';
  }

  function nodePreprocess(state) {
    var norm = normalizeText(state.question);
    var entities = resolveEntities(norm.text);
    var level = detectLevel(norm.text);

    state.normalized = norm.text;
    state.corrections = norm.fixes;
    state.entities = entities;
    state.level = level;
    state.regenerate_count = state.regenerate_count || 0;
    state.trace.push(
      '① 전처리 · 오타교정 ' + norm.fixes.length + '건 · 엔티티 ' +
      (entities.length ? pyReprList(entities) : '없음') + ' · 수준 ' + level
    );
    return state;
  }

  /* ────────────────────────────────────────────────────────────────────
   * 7. ② 의도 분류 — src/nodes/classify.py
   * ──────────────────────────────────────────────────────────────── */

  function ruleClassify(text, entities) {
    var i, j, k;

    // 규제 경로(RULES[0])는 항상 최우선으로 검사한다.
    var regulatory = DB.classifyRules.length ? DB.classifyRules[0][1] : [];
    for (i = 0; i < regulatory.length; i++) {
      k = regulatory[i];
      if (text.indexOf(k) !== -1) {
        return { label: 'out_of_scope', conf: 0.85, reason: "규칙: 규제 신호어 '" + k + "'" };
      }
    }

    // 특정 상품을 지목하지 않은 정의형 질문은 개념 설명으로 본다.
    if (!entities.length && includesAny(text, DB.definitionHints)) {
      return { label: 'general', conf: 0.7, reason: '규칙: 엔티티 없는 정의형 질문' };
    }

    for (i = 0; i < DB.classifyRules.length; i++) {
      var label = DB.classifyRules[i][0];
      var keys = DB.classifyRules[i][1];
      for (j = 0; j < keys.length; j++) {
        if (text.indexOf(keys[j]) !== -1) {
          return { label: label, conf: 0.75, reason: "규칙: '" + keys[j] + "' 신호어 매칭" };
        }
      }
    }

    if (entities.length) return { label: 'etf_info', conf: 0.6, reason: '규칙: 상품 엔티티 존재' };
    return { label: 'general', conf: 0.4, reason: '규칙: 기본값' };
  }

  function nodeClassify(state) {
    // rule 백엔드 = llm.available() False → 규칙 결과를 그대로 사용
    var res = ruleClassify(state.normalized, state.entities || []);
    state.intent = res.label;
    state.intent_confidence = res.conf;
    state.intent_reason = res.reason;
    state.trace.push('② 의도분류 · ' + res.label + ' (conf ' + fmt2(res.conf) + ') · ' + res.reason);
    return state;
  }

  /* ────────────────────────────────────────────────────────────────────
   * 8. ③ 기능별 검색 — src/nodes/retrieve.py
   * ──────────────────────────────────────────────────────────────── */

  function nodeRetrieve(state) {
    var intent = state.intent || 'general';
    var query = state.normalized;
    var entities = state.entities || [];
    var i;

    var dbRecords = [];
    if ((intent === 'etf_info' || intent === 'disclosure') && entities.length) {
      for (i = 0; i < entities.length; i++) dbRecords.push(formatRecord(entities[i]));
    }

    var stores = DB.route[intent] || [];
    var evidence = [];
    for (i = 0; i < stores.length; i++) {
      var store = DB.stores[stores[i]];
      if (!store) continue;
      var hits = store.search(query, 3, DB.config.MIN_SIMILARITY);
      for (var h = 0; h < hits.length; h++) evidence.push(hits[h]);
      if (evidence.length >= 4) break;
    }

    // Python sorted(..., reverse=True) 는 안정 정렬 — 동점은 삽입 순서 유지
    var decorated = [];
    for (i = 0; i < evidence.length; i++) decorated.push({ d: evidence[i], i: i });
    decorated.sort(function (a, b) {
      if (b.d.score > a.d.score) return 1;
      if (b.d.score < a.d.score) return -1;
      return a.i - b.i;
    });
    evidence = [];
    for (i = 0; i < decorated.length && i < 4; i++) evidence.push(decorated[i].d);

    var usedParts = [];
    for (i = 0; i < stores.length; i++) {
      var s = DB.stores[stores[i]];
      usedParts.push(s ? s.label : stores[i]);
    }
    var used = stores.length ? usedParts.join(', ') : '조회 안 함';

    state.store_used = used;
    state.evidence = evidence;
    state.db_records = dbRecords;
    state.trace.push(
      '③ 검색 · 창고[' + used + '] · 문서 ' + evidence.length + '건 · DB레코드 ' + dbRecords.length + '건'
    );
    return state;
  }

  /* ────────────────────────────────────────────────────────────────────
   * 9. ④ 답변 생성 — src/nodes/generate.py
   * ──────────────────────────────────────────────────────────────── */

  function summarizeRecord(rec) {
    var raw = splitLines(rec);
    var lines = [];
    var i;
    for (i = 0; i < raw.length; i++) lines.push(pyStrip(pyStrip(raw[i], '- ')));

    var name = pyStrip(lines[0], '[]');
    var fields = {};
    for (i = 1; i < lines.length; i++) {
      var l = lines[i];
      var p = l.indexOf(':');
      if (p !== -1) fields[pyStrip(l.slice(0, p))] = pyStrip(l.slice(p + 1));
    }

    var bits = [];
    if (hasOwn(fields, '기초지수')) bits.push('기초지수는 ' + fields['기초지수']);
    if (hasOwn(fields, '총보수')) bits.push('총보수는 ' + fields['총보수']);
    if (hasOwn(fields, '순자산')) bits.push('순자산은 ' + fields['순자산']);
    if (hasOwn(fields, '분배금 주기')) bits.push('분배금은 ' + fields['분배금 주기'] + ' 지급');

    var head = bits.length ? name + '의 ' + bits.join(', ') + '입니다.' : name + ' 정보입니다.';
    if (hasOwn(fields, '주요 구성종목')) head += ' 주요 구성종목은 ' + fields['주요 구성종목'] + ' 등입니다.';
    return head;
  }

  function templateAnswer(state) {
    var parts = [];
    var i;
    var recs = state.db_records || [];
    for (i = 0; i < recs.length; i++) parts.push(summarizeRecord(recs[i]));
    var ev = state.evidence || [];
    for (i = 0; i < ev.length && i < 2; i++) parts.push(ev[i].text);
    if (!parts.length) return DB.noEvidence;
    var body = parts.join(' ');
    if (state.level === 'beginner') return body + ' 추가로 궁금하신 점이 있으면 말씀해 주세요.';
    return body;
  }

  function nodeGenerate(state) {
    if (state.intent === 'out_of_scope') {
      state.draft = DB.refusal;
      state.trace.push('④ 생성 · 범위 외 요청 → 정중한 거절문 사용');
      return state;
    }
    if (!(state.evidence && state.evidence.length) && !(state.db_records && state.db_records.length)) {
      state.draft = DB.noEvidence;
      state.trace.push("④ 생성 · 근거 없음 → I don't know 응답");
      return state;
    }
    // rule 백엔드 = llm.available() False → 템플릿 경로
    state.draft = templateAnswer(state);
    state.trace.push('④ 생성 · 템플릿(rule 백엔드)');
    return state;
  }

  /* ────────────────────────────────────────────────────────────────────
   * 10. ⑤ 컴플라이언스 게이트 — src/nodes/compliance.py
   * ──────────────────────────────────────────────────────────────── */

  function approvedTexts() {
    return [pyStrip(DB.refusal), pyStrip(DB.noEvidence), pyStrip(DB.safeFallback)];
  }

  function evidenceText(state) {
    var parts = [];
    var i;
    var recs = state.db_records || [];
    for (i = 0; i < recs.length; i++) parts.push(recs[i]);
    var ev = state.evidence || [];
    for (i = 0; i < ev.length; i++) parts.push(ev[i].text);
    return parts.join('\n');
  }

  function complianceCheck(draft, state) {
    // 준법 검토를 마친 정형 문구는 검사 대상에서 제외한다.
    if (approvedTexts().indexOf(pyStrip(draft)) !== -1) return [];

    // 법정 용어(투자권유자문인력 등)를 마스킹해 규칙 오탐을 막는다.
    var scan = String(draft);
    var i;
    for (i = 0; i < DB.safeTerms.length; i++) {
      var t = DB.safeTerms[i];
      var mask = new Array(codePoints(t).length + 1).join('○');
      scan = replaceAll(scan, t, mask);
    }

    var violations = [];
    for (i = 0; i < DB.complianceRules.length; i++) {
      var rid = DB.complianceRules[i][0];
      var re = DB.complianceRules[i][1];
      var reason = DB.complianceRules[i][2];
      re.lastIndex = 0;
      if (re.test(scan)) violations.push(rid + ' ' + reason);
      re.lastIndex = 0;
    }

    // 근거에 없는 수치 주장 탐지 (환각 방어)
    //   파이썬은 set 을 순회하다 첫 미검증 수치에서 break 하므로, 한 답변에 미검증
    //   수치가 여러 개면 괄호 안에 예시로 붙는 수치가 실행마다 달라진다(해시 시드 의존).
    //   JS 는 등장 순서대로 검사한다 — 규칙 ID·위반 여부·건수는 같고 예시 수치만 고정된다.
    var ev = evidenceText(state);
    if (ev) {
      var flatEv = replaceAll(ev, ' ', '');
      var seen = {};
      var m;
      NUM_RE.lastIndex = 0;
      while ((m = NUM_RE.exec(scan)) !== null) {
        var token = replaceAll(m[0], ' ', '');
        if (hasOwn(seen, token)) continue;
        seen[token] = true;
        if (flatEv.indexOf(token) === -1) {
          violations.push('C-08 근거에 없는 수치 사용(' + token + ')');
          break;
        }
      }
      NUM_RE.lastIndex = 0;
    }
    return violations;
  }

  function nodeCompliance(state) {
    var draft = state.draft || '';
    var violations = complianceCheck(draft, state);
    var count = state.regenerate_count || 0;
    var i;

    if (!violations.length) {
      var citations = [];
      var recs = state.db_records || [];
      for (i = 0; i < recs.length; i++) citations.push(pyStrip(splitLines(recs[i])[0], '[]'));
      var ev = state.evidence || [];
      for (i = 0; i < ev.length; i++) citations.push(ev[i].store + ' · ' + ev[i].source);
      state.verdict = 'pass';
      state.violations = [];
      state.answer = draft;
      state.citations = citations;
      state.trace.push('⑤ 검증 게이트 · 통과');
      return state;
    }

    if (count >= DB.config.MAX_REGENERATE) {
      state.verdict = 'reject';
      state.violations = violations;
      state.answer = DB.safeFallback;
      state.citations = [];
      state.trace.push('⑤ 검증 게이트 · 반려(' + violations.join(', ') + ') · 한도 초과 → 안전 응답 대체');
      return state;
    }

    state.verdict = 'reject';
    state.violations = violations;
    state.regenerate_count = count + 1;
    state.trace.push('⑤ 검증 게이트 · 반려(' + violations.join(', ') + ') → ④로 재생성 (#' + (count + 1) + ')');
    return state;
  }

  /* ────────────────────────────────────────────────────────────────────
   * 11. 그래프 실행 — src/graph.py (반려 루프)
   * ──────────────────────────────────────────────────────────────── */

  function routeAfterCompliance(state) {
    if (state.verdict === 'pass') return 'end';
    if (state.answer) return 'end'; // 한도 초과 → 안전 응답 확정
    return 'regenerate';
  }

  function ask(question) {
    requireReady();
    var state = {
      question: String(question === undefined || question === null ? '' : question),
      trace: [],
      regenerate_count: 0
    };

    nodePreprocess(state);
    nodeClassify(state);
    nodeRetrieve(state);

    var guard = 0;
    for (;;) {
      nodeGenerate(state);
      nodeCompliance(state);
      if (routeAfterCompliance(state) === 'end') break;
      if (++guard > 10) break; // 방어적 상한 — 정상 흐름에서는 도달하지 않는다
    }

    return {
      question: state.question,
      normalized: state.normalized,
      corrections: state.corrections || [],
      entities: state.entities || [],
      level: state.level,
      intent: state.intent,
      intent_confidence: state.intent_confidence,
      intent_reason: state.intent_reason,
      store_used: state.store_used,
      evidence: state.evidence || [],
      db_records: state.db_records || [],
      answer: state.answer || '',
      citations: state.citations || [],
      trace: state.trace || [],
      verdict: state.verdict,
      violations: state.violations || [],
      regenerate_count: state.regenerate_count || 0
    };
  }

  /** 창고별 문서 수 — src/data/vectorstore.py stats() */
  function stats() {
    requireReady();
    var out = {};
    for (var i = 0; i < DB.storeOrder.length; i++) {
      var s = DB.stores[DB.storeOrder[i]];
      out[s.label] = s.docs.length;
    }
    return out;
  }

  var api = {
    init: init,
    ask: ask,
    stats: stats,
    backendInfo: function () {
      return 'rule (LLM 없이 규칙 기반 — 키 불필요)';
    },
    // 디버깅·테스트용 내부 노출
    _internal: {
      normalizeText: normalizeText,
      detectLevel: detectLevel,
      resolveEntities: resolveEntities,
      formatRecord: formatRecord,
      ruleClassify: ruleClassify,
      summarizeRecord: summarizeRecord,
      complianceCheck: complianceCheck,
      charWbNgrams: charWbNgrams,
      TfidfIndex: TfidfIndex,
      Store: Store,
      db: DB,
      labels: LABELS
    }
  };

  root.ETFAgent = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : typeof globalThis !== 'undefined' ? globalThis : this);
