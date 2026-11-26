# Token haritası (token-lab)

[English](README.md) · **Türkçe**

Claude Code token'larının nereye gittiğini gör, gerekmeyenleri kes. token-lab bilgisayarındaki kendi Claude Code geçmişini okur ve sade rakamlarla gösterir: Claude her mesajında neyi yeniden okuyor, ne hiç kullanılmıyor, neyi değiştirmelisin. Sonra talimat dosyalarını seninle birlikte gözden geçirir ve onları ancak senin onayınla değiştirir.

> **Deneme sürümü (0.2).** Windows, macOS ve Linux'ta çalışır; birkaç kişi kendi bilgisayarında denedi. Sorun ya da garip sonuç: [Issues](https://github.com/merttrkr/claude-token-kit/issues).

Örnek bulgular:

- "41 skill her mesajda listeleniyor, sen 3'ünü kullandın: her seferinde 4 bin token."
- "Bağlı bir servisin (MCP) 9 aracı 30 günde hiç kullanılmadı."
- "Uzun aralardan sonra Claude bütün konuşmayı 277 kez baştan yeniden okudu."
- "Bir dosya aynı oturumda 22 kez baştan okundu."

Her bulgunun yanında ne kadar tuttuğu, kanıtı ve tek cümlelik çözümü yazar. Rakamlar para değil token: aboneliğinin sınırlarına nasıl yansıdığı ölçülmedi.

## Güvenlik

- **Yalnızca senin bilgisayarında okur:** `~/.claude/projects` altındaki Claude Code geçmişin, `~/.claude/agents`, `~/.claude/skills` ve geçmişte adı geçen hafıza dizinleri (`MEMORY.md`).
- **Bir şey göndermez.** token-lab'in kendisi ağa çıkmaz. İndirme yalnızca kurulumda (`uv`) ve ilk çalıştırmada (token-lab'in kendisi, gerekirse Python) olur. Claude Code içinde Claude raporu konuşmandaki herhangi bir dosya gibi okur; yani rapor Claude konuşman nereye gidiyorsa oraya gider.
- **Onayın olmadan hiçbir şeyi değiştirmez.** Ayarların, CLAUDE.md, hafıza, skill'ler ve ajanlar ancak onay verdiğinde değişir: her değişikliğe tek tek ya da bir kez "hepsini yap, sonda göster"e; dosyanın yanında yedek kalır (`.bak-token-lab-…`).
- **Kendi dosyaları:** trend için her çalıştırmada `~/.claude/token-lab/history.jsonl` dosyasına bir satır rakam (ad, yol ya da metin yok; `--no-history` kapatır) ve sonra sildiği geçici dosyalar. Tam liste [SECURITY.md](SECURITY.md)'de.
- **Arkada çalışan bir şey bırakmaz:** hook, arka plan programı, zamanlanmış görev yok.

## Kurulum (yaklaşık 5 dakika)

1. **Bir terminal penceresi aç** (Claude Code değil):
   - Windows: Başlat'a bas, **PowerShell** yaz, aç.
   - macOS: Cmd+Boşluk'a bas, **Terminal** yaz, aç.
2. **Şu komutu yapıştır ve Enter'a bas.**

   Windows:

   ```powershell
   irm https://raw.githubusercontent.com/merttrkr/claude-token-kit/v0.2.8/install.ps1 | iex
   ```

   macOS / Linux:

   ```sh
   curl -fsSL https://raw.githubusercontent.com/merttrkr/claude-token-kit/v0.2.8/install.sh | sh
   ```

3. **"Installed." yazısını bekle.** İlk seferde 1–3 dakika sürer. Başka bir şey çıkarsa mesaj ne yapacağını söyler.
4. **Claude Code'u tamamen kapat** (bütün pencereleri) ve yeniden aç.
5. **Claude Code'da `/token-lab:token-lab` yaz** ve Enter'a bas. Claude bir komut çalıştırmak için izin ister: **Yes** de. Rapor yaklaşık bir dakika sürer; sonra Claude raporu anlatır ve dosyalarına birlikte bakmayı önerir.

Kurulum ne yapar: Claude Code var mı bakar, yoksa `uv`'yi (token-lab'in üzerinde çalıştığı küçük bir araç) kurar, token-lab'i Claude Code'a ekler ve otomatik güncellemesini açar. Başka bir şey yapmaz; betikler kısa: [`install.ps1`](install.ps1), [`install.sh`](install.sh).

İstersen Claude Code'a bu sayfanın linkini verip **"token-lab'i kur"** de; sonra 4. ve 5. adımları yap.

**Güncellemeler** Claude Code'u açtıktan birkaç dakika sonra kendiliğinden gelir; Claude "Plugin updated · Run /reload-plugins" der: `/reload-plugins` yaz. Hemen güncellemek için kurulum komutunu yeniden yapıştır. Sürümünü görmek için: Claude Code'da `/plugin` yaz ve **Installed**'ı aç; rapor da ilk satırında gösterir.

> **Claude Code için, kullanıcı token-lab'i kurmasını istediğinde:**
> 1. Windows'ta PowerShell'de çalıştır: `irm https://raw.githubusercontent.com/merttrkr/claude-token-kit/v0.2.8/install.ps1 | iex`. macOS ya da Linux'ta çalıştır: `curl -fsSL https://raw.githubusercontent.com/merttrkr/claude-token-kit/v0.2.8/install.sh | sh`
> 2. Çıktıda "Installed" yoksa, mesajı kullanıcıya olduğu gibi söyle ve dur.
> 3. Kullanıcıya şunu söyle: "Kuruldu. Claude Code'u tamamen kapatıp yeniden aç, sonra `/token-lab:token-lab` yaz."
> 4. Kurulum dışında hiçbir ayarı değiştirme.

**Elle kurulum,** aynı terminal penceresinde (Claude Code'da değil): [uv](https://docs.astral.sh/uv/)'yi kur (Windows: `winget install astral-sh.uv`; macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`), pencereyi kapatıp yeniden aç, sonra:

```
claude plugin marketplace add https://github.com/merttrkr/claude-token-kit
claude plugin install token-lab@token-kit
```

Sonra 4. ve 5. adımlar. Otomatik güncelleme: Claude Code'da `/plugin` yaz, Marketplaces'i aç, token-kit'i seç.

**Kurmadan, yalnızca rapor** (`uv` ve `git` gerekir):

```
uvx --from git+https://github.com/merttrkr/claude-token-kit tokenlab why
```

## Rapor nasıl okunur

Raporun metni İngilizce. `/token-lab:token-lab` onu Claude'a senin dilinde özetletir; başlık adları aşağıda parantez içinde.

- **En üst:** ana rakamlarla iki cümle, sonra her açıya bir satırlık **Özet** (Summary) tablosu (uzun oturumlar, önbellek, araç çıktıları, CLAUDE.md, hafıza, araçlar ve MCP, skill'ler, ajanlar, hook'lar; uzun oturumlar ve önbellek gözlemdir, kesilebilir sayılmaz; maliyetteki payı en büyük olan önce) ve **İlk adım** (First step).
- **1. bölüm:** her istekte tekrar giden kısım ve payı.
- **2. bölüm:** olmaması gerekenler, haftalık maliyete göre sıralı. Sonunda **ajan adayları** (Agent candidates) tablosu var: tekrar eden işler (general-purpose ajanlarda ya da ana oturumun kendisi okuduğu dış sistemlerde) ajan standardına göre ölçülür, tahmini haftalık kazancıyla.
  - Maliyet "girdi-eşdeğeri token"dır: önbellekten (Claude'un yeniden kullandığı, ucuz okunan kısım) okunan token yaklaşık 0.1, önbelleğe yazılan 1.25–2 sayılır (Claude Code'un fiyat oranları).
  - Abonelikte bunun kotaya etkisi ölçülmedi.
- **"Zaten düzelmiş görünüyor" (already looks fixed):** son 2 günün oturumlarında artık görünmeyen öğeler. Önerilmez.
- **3. bölüm:** raporun göremedikleri. Oturum başlığı ve özetleme (compaction: uzun konuşmanın kısaltılması) istekleri kayıtlara yazılmıyor.
- **4. bölüm:** en çok taşınan dosyalar. İstersen Claude bunları okuyup gereksiz satırları bulur. Bu kısım model yargısıdır.
- **5. bölüm:** ajan işleri. Hangi tekrar eden iş isimli bir ajana taşınabilir, hangi ajan kullanılmıyor.

Rapor ne kadar geriye bakabilir? Claude Code eski kayıtları `cleanupPeriodDays` ayarına göre siler (varsayılan 30 gün). Bu ayar senin.

**Gelişim:** ikinci çalıştırmadan itibaren en üstteki **Trend** (Trend) tablosu istek başına sabit kısmı, kesilebilir israfı ve her açının maliyet payını son çalıştırmayla ve yaklaşık bir ay önceki çalıştırmayla karşılaştırır. İlk çalıştırma daha önceki bir noktayı da bir kez ölçer (iki hafta önce; geçmiş daha kısaysa ortası; iki günden kısaysa hiç); trend ilk günden görünür, biraz daha uzun sürer.

**Bir değişiklik işe yaradı mı?** `uvx --from git+https://github.com/merttrkr/claude-token-kit tokenlab why --until 2026-11-10` raporu o günün başına (UTC) kadarki konuşma kayıtlarından çıkarır; diskteki dosyalar (hafıza, ajanlar, skill'ler) bugünkü hâliyle okunur. Özet'ini bugünküyle karşılaştır; ya da `/token-lab:token-lab` içinde Claude'dan bir tarihten önce ve sonrasını karşılaştırmasını iste.

## Dosya incelemesi: `/token-lab:optimize`

Rapor ölçer; bu inceleme okur. Claude bağlamına giren dosyaları (CLAUDE.md'ler, kurallar, hafıza, skill'ler, ajanlar) en pahalı 10'undan başlayarak (istersen genişletirsin) yerleşim standardına göre okur ve her satır için öneri verir: sil, kısalt, taşı (nereye) ya da birleştir. Eklenebilecekleri de önerir: tekrar eden bir araç hatası için hook, her oturumda okuduğun bir dosya için hafıza satırı, tekrar tekrar yaptığın bir düzeltme için kural.

- Önce bir betik kesin olarak kontrol edilebilenleri listeler: artık olmayan yollar, system prompt'un zaten söylediği cümleler, aynı satırları paylaşan dosyalar.
- Rapor buna götürür: özetten sonra Claude dosyalarınla devam etmeyi önerir. İstersen doğrudan da başlatabilirsin.
- Claude hangi dosyalarla başlayacağını ve eklenebilecekler için geçmiş konuşmalarına bakıp bakmayacağını sorar.
- Claude önce nasıl uygulayacağını sorar: tek tek (her birinde fark, yedek ve onayın) ya da hepsi birden (her dosyanın yedeği alınır, bütün değişiklikler sonda gösterilir).
- İncelemenin kabaca maliyetini baştan söyler; tek tek modunda bu oturumda (daha ucuz) ya da paralel okuyucularla (daha hızlı) okumayı da sana bırakır.

## Kendi kuralların

Eşikler ve alan adları `src/tokenlab/rules.py` içinde. Değiştirmek için `~/.claude/token-lab.toml` yaz:

```toml
[why]
min_weekly_tokens = 50000

[[areas]]            # işlerini sınıflarken kullanılacak klasörler
name = "specs"
path = "/specs/"

[external]           # aynı sistemi gösteren MCP sunucuları
"mcp-atlassian" = "jira"
```

## Geri bildirim

Sorun ya da öneri için bu reponun **Issues** sayfası. Konuşma metni, dosya yolu ya da şirket bilgisi yapıştırma; issue'lar herkese açık.

## Kaldırma

Bir terminal penceresinde (Claude Code'da değil):

```
claude plugin uninstall token-lab@token-kit
claude plugin marketplace remove token-kit
```

Geride kalanlar, onları da silmek istersen:

- Raporun geçmişi: `~/.claude/token-lab` klasörünü sil.
- Dosya incelemesinin değişen dosyaların yanına aldığı yedekler: `*.bak-token-lab-*`.
- `uv` ve indirdiği Python ile paketler: `uv cache clean`, sonra uv'nin kendisi (Windows: `winget uninstall astral-sh.uv`; macOS/Linux: [uv belgeleri](https://docs.astral.sh/uv/getting-started/installation/#uninstallation)).

## Geliştirme

```
uv sync
uv run pytest -q
uv run ruff check . && uv run mypy src
```

Desteklenen komutlar `why` ve `inventory`; diğerleri araştırma araçları, duruyor ama desteklenmiyor. Bkz. [CONTRIBUTING.md](CONTRIBUTING.md), [CHANGELOG.md](CHANGELOG.md), [SECURITY.md](SECURITY.md).
