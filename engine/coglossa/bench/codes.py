"""FLORES-200 file code (`spa_Latn`) -> BCP-47 tag (`es`), for all 204 language varieties.

Source: the file names in `flores200_dataset/dev` of the archive at `flores.FLORES_URL` (the
checksum is pinned as `flores.FLORES_SHA256`; dev and devtest hold the same 204 codes), mapped
with the SIL ISO 639-3 code tables (iso-639-3.tab, iso-639-3-macrolanguages.tab) and the IANA
language subtag registry.

The rule for each `<lang>_<Script>` code:
- the language is its ISO 639-1 code when the 639-3 code has one (`spa` -> `es`);
- an individual language that FLORES uses for a macrolanguage takes the macrolanguage's 639-1
  code (`arb` -> `ar`, `pes` -> `fa`, `zsm` -> `ms`, `swh` -> `sw`, `knc` -> `kr`, ...); where
  FLORES has two members of one macrolanguage, the standard one takes it (`azj` -> `az`, not
  `azb`; `pes` -> `fa`, not `prs`; `lvs` -> `lv`, not `ltg`; `kmr` -> `ku` as in CLDR, not
  `ckb`; the Arabic dialects keep their own codes);
- otherwise the 639-3 code stays (`ast`, `ceb`, `ajp`, which ISO retired into `apc` in 2023 but
  FLORES keeps as its own file);
- the script subtag is added only when FLORES has the language in more than one script
  (`zh-Hans`/`zh-Hant`, `ks-Arab`/`ks-Deva`), and never for the language's IANA Suppress-Script
  (`arb_Arab` is `ar`, `arb_Latn` is `ar-Latn`).

To regenerate: list `flores200_dataset/dev` of a fresh `coglossa bench fetch-flores`, apply the
rule above to every code, and check every tag is unique.
"""

FLORES_CODES: dict[str, str] = {
    "ace_Arab": "ace-Arab",  # Achinese
    "ace_Latn": "ace-Latn",  # Achinese
    "acm_Arab": "acm",  # Mesopotamian Arabic
    "acq_Arab": "acq",  # Ta'izzi-Adeni Arabic
    "aeb_Arab": "aeb",  # Tunisian Arabic
    "afr_Latn": "af",  # Afrikaans
    "ajp_Arab": "ajp",  # South Levantine Arabic
    "aka_Latn": "ak",  # Akan
    "als_Latn": "sq",  # Tosk Albanian
    "amh_Ethi": "am",  # Amharic
    "apc_Arab": "apc",  # Levantine Arabic
    "arb_Arab": "ar",  # Standard Arabic
    "arb_Latn": "ar-Latn",  # Standard Arabic
    "ars_Arab": "ars",  # Najdi Arabic
    "ary_Arab": "ary",  # Moroccan Arabic
    "arz_Arab": "arz",  # Egyptian Arabic
    "asm_Beng": "as",  # Assamese
    "ast_Latn": "ast",  # Asturian
    "awa_Deva": "awa",  # Awadhi
    "ayr_Latn": "ay",  # Central Aymara
    "azb_Arab": "azb",  # South Azerbaijani
    "azj_Latn": "az",  # North Azerbaijani
    "bak_Cyrl": "ba",  # Bashkir
    "bam_Latn": "bm",  # Bambara
    "ban_Latn": "ban",  # Balinese
    "bel_Cyrl": "be",  # Belarusian
    "bem_Latn": "bem",  # Bemba (Zambia)
    "ben_Beng": "bn",  # Bengali
    "bho_Deva": "bho",  # Bhojpuri
    "bjn_Arab": "bjn-Arab",  # Banjar
    "bjn_Latn": "bjn-Latn",  # Banjar
    "bod_Tibt": "bo",  # Tibetan
    "bos_Latn": "bs",  # Bosnian
    "bug_Latn": "bug",  # Buginese
    "bul_Cyrl": "bg",  # Bulgarian
    "cat_Latn": "ca",  # Catalan
    "ceb_Latn": "ceb",  # Cebuano
    "ces_Latn": "cs",  # Czech
    "cjk_Latn": "cjk",  # Chokwe
    "ckb_Arab": "ckb",  # Central Kurdish
    "crh_Latn": "crh",  # Crimean Tatar
    "cym_Latn": "cy",  # Welsh
    "dan_Latn": "da",  # Danish
    "deu_Latn": "de",  # German
    "dik_Latn": "dik",  # Southwestern Dinka
    "dyu_Latn": "dyu",  # Dyula
    "dzo_Tibt": "dz",  # Dzongkha
    "ell_Grek": "el",  # Modern Greek (1453-)
    "eng_Latn": "en",  # English
    "epo_Latn": "eo",  # Esperanto
    "est_Latn": "et",  # Estonian
    "eus_Latn": "eu",  # Basque
    "ewe_Latn": "ee",  # Ewe
    "fao_Latn": "fo",  # Faroese
    "fij_Latn": "fj",  # Fijian
    "fin_Latn": "fi",  # Finnish
    "fon_Latn": "fon",  # Fon
    "fra_Latn": "fr",  # French
    "fur_Latn": "fur",  # Friulian
    "fuv_Latn": "ff",  # Nigerian Fulfulde
    "gaz_Latn": "om",  # West Central Oromo
    "gla_Latn": "gd",  # Scottish Gaelic
    "gle_Latn": "ga",  # Irish
    "glg_Latn": "gl",  # Galician
    "grn_Latn": "gn",  # Guarani
    "guj_Gujr": "gu",  # Gujarati
    "hat_Latn": "ht",  # Haitian
    "hau_Latn": "ha",  # Hausa
    "heb_Hebr": "he",  # Hebrew
    "hin_Deva": "hi",  # Hindi
    "hne_Deva": "hne",  # Chhattisgarhi
    "hrv_Latn": "hr",  # Croatian
    "hun_Latn": "hu",  # Hungarian
    "hye_Armn": "hy",  # Armenian
    "ibo_Latn": "ig",  # Igbo
    "ilo_Latn": "ilo",  # Iloko
    "ind_Latn": "id",  # Indonesian
    "isl_Latn": "is",  # Icelandic
    "ita_Latn": "it",  # Italian
    "jav_Latn": "jv",  # Javanese
    "jpn_Jpan": "ja",  # Japanese
    "kab_Latn": "kab",  # Kabyle
    "kac_Latn": "kac",  # Kachin
    "kam_Latn": "kam",  # Kamba (Kenya)
    "kan_Knda": "kn",  # Kannada
    "kas_Arab": "ks-Arab",  # Kashmiri
    "kas_Deva": "ks-Deva",  # Kashmiri
    "kat_Geor": "ka",  # Georgian
    "kaz_Cyrl": "kk",  # Kazakh
    "kbp_Latn": "kbp",  # Kabiyè
    "kea_Latn": "kea",  # Kabuverdianu
    "khk_Cyrl": "mn",  # Halh Mongolian
    "khm_Khmr": "km",  # Khmer
    "kik_Latn": "ki",  # Kikuyu
    "kin_Latn": "rw",  # Kinyarwanda
    "kir_Cyrl": "ky",  # Kirghiz
    "kmb_Latn": "kmb",  # Kimbundu
    "kmr_Latn": "ku",  # Northern Kurdish
    "knc_Arab": "kr-Arab",  # Central Kanuri
    "knc_Latn": "kr-Latn",  # Central Kanuri
    "kon_Latn": "kg",  # Kongo
    "kor_Hang": "ko",  # Korean
    "lao_Laoo": "lo",  # Lao
    "lij_Latn": "lij",  # Ligurian
    "lim_Latn": "li",  # Limburgan
    "lin_Latn": "ln",  # Lingala
    "lit_Latn": "lt",  # Lithuanian
    "lmo_Latn": "lmo",  # Lombard
    "ltg_Latn": "ltg",  # Latgalian
    "ltz_Latn": "lb",  # Luxembourgish
    "lua_Latn": "lua",  # Luba-Lulua
    "lug_Latn": "lg",  # Ganda
    "luo_Latn": "luo",  # Luo (Kenya and Tanzania)
    "lus_Latn": "lus",  # Lushai
    "lvs_Latn": "lv",  # Standard Latvian
    "mag_Deva": "mag",  # Magahi
    "mai_Deva": "mai",  # Maithili
    "mal_Mlym": "ml",  # Malayalam
    "mar_Deva": "mr",  # Marathi
    "min_Arab": "min-Arab",  # Minangkabau
    "min_Latn": "min-Latn",  # Minangkabau
    "mkd_Cyrl": "mk",  # Macedonian
    "mlt_Latn": "mt",  # Maltese
    "mni_Beng": "mni",  # Manipuri
    "mos_Latn": "mos",  # Mossi
    "mri_Latn": "mi",  # Maori
    "mya_Mymr": "my",  # Burmese
    "nld_Latn": "nl",  # Dutch
    "nno_Latn": "nn",  # Norwegian Nynorsk
    "nob_Latn": "nb",  # Norwegian Bokmål
    "npi_Deva": "ne",  # Nepali (individual language)
    "nso_Latn": "nso",  # Pedi
    "nus_Latn": "nus",  # Nuer
    "nya_Latn": "ny",  # Chichewa
    "oci_Latn": "oc",  # Occitan (post 1500)
    "ory_Orya": "or",  # Odia
    "pag_Latn": "pag",  # Pangasinan
    "pan_Guru": "pa",  # Panjabi
    "pap_Latn": "pap",  # Papiamento
    "pbt_Arab": "ps",  # Southern Pashto
    "pes_Arab": "fa",  # Iranian Persian
    "plt_Latn": "mg",  # Plateau Malagasy
    "pol_Latn": "pl",  # Polish
    "por_Latn": "pt",  # Portuguese
    "prs_Arab": "prs",  # Dari
    "quy_Latn": "qu",  # Ayacucho Quechua
    "ron_Latn": "ro",  # Romanian
    "run_Latn": "rn",  # Rundi
    "rus_Cyrl": "ru",  # Russian
    "sag_Latn": "sg",  # Sango
    "san_Deva": "sa",  # Sanskrit
    "sat_Olck": "sat",  # Santali
    "scn_Latn": "scn",  # Sicilian
    "shn_Mymr": "shn",  # Shan
    "sin_Sinh": "si",  # Sinhala
    "slk_Latn": "sk",  # Slovak
    "slv_Latn": "sl",  # Slovenian
    "smo_Latn": "sm",  # Samoan
    "sna_Latn": "sn",  # Shona
    "snd_Arab": "sd",  # Sindhi
    "som_Latn": "so",  # Somali
    "sot_Latn": "st",  # Southern Sotho
    "spa_Latn": "es",  # Spanish
    "srd_Latn": "sc",  # Sardinian
    "srp_Cyrl": "sr",  # Serbian
    "ssw_Latn": "ss",  # Swati
    "sun_Latn": "su",  # Sundanese
    "swe_Latn": "sv",  # Swedish
    "swh_Latn": "sw",  # Swahili (individual language)
    "szl_Latn": "szl",  # Silesian
    "tam_Taml": "ta",  # Tamil
    "taq_Latn": "taq-Latn",  # Tamasheq
    "taq_Tfng": "taq-Tfng",  # Tamasheq
    "tat_Cyrl": "tt",  # Tatar
    "tel_Telu": "te",  # Telugu
    "tgk_Cyrl": "tg",  # Tajik
    "tgl_Latn": "tl",  # Tagalog
    "tha_Thai": "th",  # Thai
    "tir_Ethi": "ti",  # Tigrinya
    "tpi_Latn": "tpi",  # Tok Pisin
    "tsn_Latn": "tn",  # Tswana
    "tso_Latn": "ts",  # Tsonga
    "tuk_Latn": "tk",  # Turkmen
    "tum_Latn": "tum",  # Tumbuka
    "tur_Latn": "tr",  # Turkish
    "twi_Latn": "tw",  # Twi
    "tzm_Tfng": "tzm",  # Central Atlas Tamazight
    "uig_Arab": "ug",  # Uighur
    "ukr_Cyrl": "uk",  # Ukrainian
    "umb_Latn": "umb",  # Umbundu
    "urd_Arab": "ur",  # Urdu
    "uzn_Latn": "uz",  # Northern Uzbek
    "vec_Latn": "vec",  # Venetian
    "vie_Latn": "vi",  # Vietnamese
    "war_Latn": "war",  # Waray (Philippines)
    "wol_Latn": "wo",  # Wolof
    "xho_Latn": "xh",  # Xhosa
    "ydd_Hebr": "yi",  # Eastern Yiddish
    "yor_Latn": "yo",  # Yoruba
    "yue_Hant": "yue",  # Yue Chinese
    "zho_Hans": "zh-Hans",  # Chinese
    "zho_Hant": "zh-Hant",  # Chinese
    "zsm_Latn": "ms",  # Standard Malay
    "zul_Latn": "zu",  # Zulu
}
