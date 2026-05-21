
$(document).ready(function () {
    var etfid = $("#ETF_ID").attr("value");
    var navdate = $("#NAV_DATE").attr("value");
    var vEqual = $("#EQUAL").attr("value");

    GetPieCountry(etfid, navdate); //國家配置圓餅圖
    GetPieCredit(etfid, navdate);  //信用評等圓餅圖
    searchFundDiv(etfid);

    searchFumdHold(etfid, vEqual, null, null);

});

$(".li-etf-click").click(function () {
    $("#SELECT_ETF_ID").val($(this).attr("title"));
    //$("form").submit();
});
$(".li-fund-click").click(function () {
    $("#SELECT_FUND_TYPE").val($(this).attr("title"));
    //$("form").submit();
});

$(".li-dropdown-click").click(function () {
    let etfId = $("#SELECT_ETF_ID").val();
    let FundType = $("#SELECT_FUND_TYPE").val();
    location.href = "/ETF/Home/ETFSeriesDetail/" + etfId + "?FundType=" + FundType;
});

// 切換 收益分配 配息年份下拉選單
$(".li-year-click").click(function (e) {

    $("#btn-select-year").attr("title", $(this).attr("title"));

    let span_text = $(this).attr("title");
    if (span_text == "ALL") span_text = "全部";

    $("#span_text").text(span_text);

    var etfId = $("#SELECT_ETF_ID").val();
    var select_year = $("#btn-select-year").attr("title");
    var select_begin_date = $("#DIV_BEG_DATE").val();
    var select_end_date = $("#DIV_END_DATE").val();

    searchFundDiv(etfId, select_year, select_begin_date, select_end_date);
});

// 收益分配 搜尋
$(".btn-funddiv").click(function () {

    var etfId = $("#SELECT_ETF_ID").val();
    var select_year = $("#btn-select-year").attr("title");
    var select_begin_date = $("#DIV_BEG_DATE").val();
    var select_end_date = $("#DIV_END_DATE").val();

    searchFundDiv(etfId, select_year, select_begin_date, select_end_date);

});

//日曆
$(".open-datetimepicker").click(function (event) {
    event.preventDefault();
    var estDate = $("#ESTABLISH_DATE").attr("value");

    $(this).parents("div").eq(1).find(":input[name*=DATE]").datepicker({ dateFormat: "yy/mm/dd", minDate: estDate, maxDate: -1 }).focus();

});

//淨值查詢
$(".btn-fundnav").click(function () {

    var etfid = $("#ETF_ID").attr("value");
    var vBegDay = $("#BEG_DATE").val();
    var vEndDay = $("#END_DATE").val();
    var vEqual = $("#EQUAL").attr("value");
    searchFumdHold(etfid, vEqual, vBegDay, vEndDay);
});

// 查詢歷史淨值
function searchFumdHold(etfid, equalValue, vBegDay, vEndDay) {

    if (equalValue != "" && equalValue != null) {
        //$("#iframe_chart_1").attr("src", vURL + "?equal=" + equalValue + "&key=1&sday=" + vBegDay + "&eday=" + vEndDay + "&v=" + vTime);
        $.ajax({
            type: "Post",
            url: "/ETF/Home/PartialHistoryFundNav",
            data: { equal: equalValue, id: etfid, sday: vBegDay, eday: vEndDay },
            async: true,
            contentType: "application/x-www-form-urlencoded; charset=utf-8",
            //dataType: "json",
            success: function (outdata) {
                $('#historyFundNavListData').html(outdata);
            },
            error: function (error) {
                alert("淨值查詢發生錯誤!");
                console.log('btn-findnav click error: ' + error);
            }
        });
    }
}


//取得國家配置圓餅圖
function GetPieCountry(etfid, navdate) {
    var alink = "/ETF/Home/CountryChartImage";
    var vETFID = etfid;
    var vDATE = navdate

    $.ajax({
        type: "post",
        url: alink,
        data: JSON.stringify({ id: vETFID, datadate: vDATE }),
        async: false, //ajax請求為同步
        contentType: "application/json; charset=utf-8",
        dataType: "json",
        success: function (jsondata) {
            if (jsondata !== null && Object.keys(jsondata).length > 0 && jsondata.data.length > 0) {
                let position = 'right';
                //if (isMobileDevice()) {
                //    position = 'bottom';
                //}

                const Labels = jsondata.labelX;
                const data = {
                    labels: Labels,
                    datasets: [{
                        label: '',
                        data: jsondata.data,
                        backgroundColor: [
                            '#fc557d', '#fc7293', '#ff9f7f', '#fedb5b', '#fff0aa', '#d1e780', '#9fe7b9', '#66e0e3', '#31c5e9', '#37a2da', '#8274e9', '#9b94f5', '#e7bcf2', '#e690d1', '#e061ae'
                            , '#9e005d', '#93278f', '#662d91', '#1b1464', '#2e3192', '#0071bc'
                        ],
                        hoverOffset: 4,
                        borderWidth: 0.1 // 外框寬度
                    }]
                };

                const config = {
                    type: 'pie',//款式doughnut
                    data,
                    options: {
                        cutoutPercentage: 20,
                        responsive: true,
                        plugins: {
                            legend: {
                                position: position,
                                labels: {
                                    font: {
                                        size: 14
                                    },
                                    textAlign: 'left',
                                    generateLabels: function (chart) { //Label 顯示數據
                                        var labels = chart.data.labels;
                                        var dataset = chart.data.datasets[0];
                                        var legend = labels.map(function (label, index) {
                                            return {
                                                datasetIndex: 0,
                                                fillStyle: dataset.backgroundColor && dataset.backgroundColor[index],
                                                strokeStyle: dataset.borderColor && dataset.borderColor[index],
                                                lineWidth: dataset.borderWidth,
                                                text: label + '  ' + dataset.data[index] + '%'

                                            };


                                        });
                                        return legend;
                                    }
                                }
                            }
                        }
                    }
                };




                if (typeof CountryChart === "object") {
                    CountryChart.destroy();
                }

                var graphareaCountry = document.getElementById("pieChartCountry").getContext("2d");
                CountryChart = new Chart(
                    graphareaCountry,
                    config
                );
            } else {
                //$("#pieChartCredit").parent().append("<div class='width_530 text-center'>— 請詳見最新基金月報 —</div>");
            }
        },
        error: function (error) {
            alert('國家配置圓餅圖 發生錯誤!');
            console.log('error: ' + error);
        }
    });
}

//取得信用評等圓餅圖
function GetPieCredit(etfid, navdate) {
    var alink = "/ETF/Home/CreditChartImage";
    var vETFID = etfid;
    var vDATE = navdate


    $.ajax({
        type: "post",
        url: alink,
        data: JSON.stringify({ id: vETFID, datadate: vDATE }),
        async: false,
        contentType: "application/json; charset=utf-8",
        dataType: "json",
        success: function (jsondata) {
            if (jsondata !== null && Object.keys(jsondata).length > 0 && jsondata.data.length > 0) {
                let position = 'right';
                //if (isMobileDevice()) {
                //    position = 'bottom';
                //}

                const Labels = jsondata.labelX;
                const data = {
                    labels: Labels,
                    datasets: [{
                        label: '',
                        data: jsondata.data,
                        backgroundColor: [
                            '#fc557d', '#fc7293', '#ff9f7f', '#fedb5b', '#fff0aa', '#d1e780', '#9fe7b9', '#66e0e3', '#31c5e9', '#37a2da', '#8274e9', '#9b94f5', '#e7bcf2', '#e690d1', '#e061ae'
                            , '#9e005d', '#93278f', '#662d91', '#1b1464', '#2e3192', '#0071bc'
                        ],
                        hoverOffset: 4,
                        borderWidth: 0.1 // 外框寬度
                    }]
                };

                const config = {
                    type: 'pie',//類別doughnut
                    data,
                    options: {
                        cutoutPercentage: 20,
                        responsive: true,
                        plugins: {
                            legend: {
                                position: position,
                                labels: {
                                    font: {
                                        size: 14
                                    },
                                    textAlign: 'left',
                                    generateLabels: function (chart) { //Label 顯示數據
                                        var labels = chart.data.labels;
                                        var dataset = chart.data.datasets[0];
                                        var legend = labels.map(function (label, index) {
                                            return {
                                                datasetIndex: 0,
                                                fillStyle: dataset.backgroundColor && dataset.backgroundColor[index],
                                                strokeStyle: dataset.borderColor && dataset.borderColor[index],
                                                lineWidth: dataset.borderWidth,
                                                text: label + '  ' + dataset.data[index] + '%'

                                            };


                                        });
                                        return legend;
                                    }
                                }
                            }
                        }
                    }
                };




                if (typeof CreditChart === "object") {
                    CreditChart.destroy();
                }

                var graphareaCredit = document.getElementById("pieChartCredit").getContext("2d");
                CreditChart = new Chart(
                    graphareaCredit,
                    config
                );
            } else {
                //$("#pieChartCredit").parent().append("<div class='width_530 text-center'>— 請詳見最新基金月報 —</div>");
            }
        },
        error: function (error) {
            alert("信用評等圓餅圖 發生錯誤!");
            console.log('error: ' + error);
        }
    });
}

// 更新收益分配 下方列表資料 帶入條件
function searchFundDiv(etf_id, select_year, select_begin_date, select_end_date) {

    $.ajax({
        type: "POST",
        url: "/ETF/Home/PartialSeriesDetailDiv",
        data: { etf_id: etf_id, select_year: select_year, select_begin_date: select_begin_date, select_end_date: select_end_date },
        async: true,
        contentType: "application/x-www-form-urlencoded; charset=utf-8",
        /*dataType: "json",*/
        success: function (outdata) {

            $('#fundDivListData').html(outdata);
        },
        error: function (error) {
            alert("收益分配查詢發生錯誤!");
            console.log('searchFundDiv error: ' + error);
        }
    });
}

