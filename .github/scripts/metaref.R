# 메타분석 표준 프로그램(R metafor)으로 논문 원자료를 계산해 CSV 로 남긴다. 사이트와 상관없다.
# 사이트 계산(meta_calc.py)과 나란히 비교하려는 기준값이다 (check_meta_ref.py 가 읽는다).
suppressMessages(library(metafor))
dir.create("ref", showWarnings = FALSE)
res <- data.frame()
add <- function(name, r, back = identity) {
  res <<- rbind(res, data.frame(name = name, est = back(as.numeric(r$b)), lo = back(r$ci.lb), hi = back(r$ci.ub),
                                tau2 = r$tau2, I2 = r$I2, Q = r$QE, k = r$k))
}
# 1) Colditz 1994 JAMA — BCG 백신, 위험비(RR)
write.csv(dat.bcg, "ref/bcg.csv", row.names = FALSE)
d <- escalc("RR", ai = tpos, bi = tneg, ci = cpos, di = cneg, data = dat.bcg)
add("BCG RR random(DL)", rma(yi, vi, data = d, method = "DL"), exp)
add("BCG RR fixed", rma(yi, vi, data = d, method = "FE"), exp)
d <- escalc("OR", ai = tpos, bi = tneg, ci = cpos, di = cneg, data = dat.bcg)
add("BCG OR random(DL)", rma(yi, vi, data = d, method = "DL"), exp)
d <- escalc("RD", ai = tpos, bi = tneg, ci = cpos, di = cneg, data = dat.bcg)
add("BCG RD random(DL)", rma(yi, vi, data = d, method = "DL"))
# 2) Hackshaw 1997 BMJ — 간접흡연과 폐암, 연구별 OR 과 95% CI
write.csv(dat.hackshaw1998, "ref/hackshaw.csv", row.names = FALSE)
add("Hackshaw OR random(DL)", rma(yi, vi, data = dat.hackshaw1998, method = "DL"), exp)
add("Hackshaw OR fixed", rma(yi, vi, data = dat.hackshaw1998, method = "FE"), exp)
# 3) Normand 1999 Stat Med — 뇌졸중 전문 병동, 재원일수(연속형)
write.csv(dat.normand1999, "ref/normand.csv", row.names = FALSE)
d <- escalc("MD", m1i = m1i, sd1i = sd1i, n1i = n1i, m2i = m2i, sd2i = sd2i, n2i = n2i, data = dat.normand1999)
add("Normand MD random(DL)", rma(yi, vi, data = d, method = "DL"))
add("Normand MD fixed", rma(yi, vi, data = d, method = "FE"))
d <- escalc("SMD", m1i = m1i, sd1i = sd1i, n1i = n1i, m2i = m2i, sd2i = sd2i, n2i = n2i, data = dat.normand1999)
add("Normand SMD random(DL)", rma(yi, vi, data = d, method = "DL"))
write.csv(res, "ref/results.csv", row.names = FALSE)
print(res, digits = 6)
cat("metafor", as.character(packageVersion("metafor")), "\n")
