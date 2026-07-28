import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtGraphs

Item {
    property string title: qsTr("Health Dashboard")
    required property var healthController

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 4
        spacing: 6

        RowLayout {
            Layout.fillWidth: true
            Label {
                text: qsTr("Health Score")
                font.bold: true
            }
            Label {
                text: healthController ? healthController.score + " / 100" : "--"
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignRight
            }
            Button {
                text: qsTr("Refresh")
                onClicked: healthController.refresh()
            }
        }

        GraphsView {
            Layout.fillWidth: true
            Layout.fillHeight: true

            BarSeries {
                axisX: BarCategoryAxis {
                    categories: [qsTr("Schema"), qsTr("Broken Link"), qsTr("Isolated")]
                }
                axisY: ValueAxis {
                    min: 0
                    max: Math.max(
                        5,
                        healthController ? healthController.schemaViolations : 0,
                        healthController ? healthController.brokenLinks : 0,
                        healthController ? healthController.isolatedNotes : 0
                    )
                }
                BarSet {
                    label: qsTr("Findings")
                    values: healthController
                        ? [
                              healthController.schemaViolations,
                              healthController.brokenLinks,
                              healthController.isolatedNotes
                          ]
                        : [0, 0, 0]
                }
            }
        }
    }
}
