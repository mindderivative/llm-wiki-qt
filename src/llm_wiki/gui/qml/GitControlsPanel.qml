import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    property string title: qsTr("Git Controls")
    required property var gitController

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 4
        spacing: 6

        RowLayout {
            Layout.fillWidth: true
            visible: gitController && !gitController.isInitialized

            Label {
                text: qsTr("Not a Git repository yet.")
                Layout.fillWidth: true
            }
            Button {
                text: qsTr("Initialize")
                onClicked: gitController.initRepo()
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: gitController && gitController.isInitialized

            RowLayout {
                Layout.fillWidth: true
                Label {
                    text: gitController
                        ? qsTr("Branch: %1 (%2)").arg(gitController.branch).arg(
                              gitController.clean ? qsTr("clean") : qsTr("dirty"))
                        : ""
                    Layout.fillWidth: true
                }
                Button {
                    text: qsTr("Refresh")
                    onClicked: gitController.refresh()
                }
            }

            ListView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: gitController ? gitController.changedFiles : null

                delegate: RowLayout {
                    width: ListView.view.width
                    Label { text: "[" + kind + "]"; opacity: 0.6 }
                    Label { text: path; Layout.fillWidth: true }
                }
            }

            RowLayout {
                Layout.fillWidth: true

                TextField {
                    id: commitMessageField
                    Layout.fillWidth: true
                    placeholderText: qsTr("Commit message")
                }
                Button {
                    text: qsTr("Stage All")
                    onClicked: gitController.stageAll()
                }
                Button {
                    text: qsTr("Commit")
                    enabled: commitMessageField.text.length > 0
                    onClicked: {
                        gitController.commit(commitMessageField.text)
                        commitMessageField.text = ""
                    }
                }
            }
        }
    }
}
