import QtQml
import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts
import LLMWiki

ApplicationWindow {
    id: root
    visible: true
    width: 1280
    height: 800
    title: appController && appController.hasVault
        ? "LLM-Wiki -- " + appController.vaultName
        : "LLM-Wiki"

    Material.theme: Material.Dark
    Material.accent: Material.Blue

    AppController {
        id: appController
        objectName: "appController"
        onErrorOccurred: (message) => {
            errorDialog.text = message
            errorDialog.open()
        }
    }

    LogModel {
        id: logModel
        objectName: "logModel"
    }

    Dialog {
        id: errorDialog
        title: qsTr("Error")
        modal: true
        standardButtons: Dialog.Ok
        anchors.centerIn: parent
        property alias text: errorLabel.text

        Label {
            id: errorLabel
            wrapMode: Text.WordWrap
        }
    }

    menuBar: MenuBar {
        Menu {
            title: qsTr("&File")

            Action {
                text: qsTr("&New Vault…")
                onTriggered: newVaultDialog.open()
            }
            Action {
                text: qsTr("&Open Vault…")
                onTriggered: openVaultDialog.open()
            }
            Menu {
                id: recentVaultsMenu
                title: qsTr("Open &Recent")
                onAboutToShow: recentInstantiator.model = appController.recentVaults()

                Instantiator {
                    id: recentInstantiator
                    model: []
                    MenuItem {
                        text: modelData
                        onTriggered: appController.openVault(modelData)
                    }
                    onObjectAdded: (index, object) => recentVaultsMenu.insertItem(index, object)
                    onObjectRemoved: (index, object) => recentVaultsMenu.removeItem(object)
                }
            }
            MenuSeparator {}
            Action {
                text: qsTr("E&xit")
                onTriggered: Qt.quit()
            }
        }
        Menu {
            title: qsTr("&Edit")
            Action {
                text: qsTr("&Settings…")
                onTriggered: settingsDialog.open()
            }
        }
        Menu {
            title: qsTr("&Windows")
            MenuItem { text: qsTr("Git Controls"); checkable: true; checked: true }
            MenuItem { text: qsTr("Queue && Raw List"); checkable: true; checked: true }
            MenuItem { text: qsTr("Health Dashboard"); checkable: true; checked: true }
            MenuItem { text: qsTr("AI Chat"); checkable: true; checked: true }
            MenuItem { text: qsTr("Pipeline Log"); checkable: true; checked: true }
        }
    }

    header: ToolBar {
        id: pipelineToolBar
        readonly property var adapter: appController.pipelineAdapter
        readonly property bool running: adapter ? adapter.running : false
        readonly property bool paused: adapter ? adapter.paused : false

        RowLayout {
            anchors.fill: parent
            anchors.margins: 4
            spacing: 4

            ToolButton {
                id: automationToggle
                text: checked ? qsTr("Auto") : qsTr("Manual")
                checkable: true
                enabled: appController.hasVault
            }
            ToolButton {
                text: qsTr("Step")
                visible: !automationToggle.checked
                enabled: appController.hasVault && !pipelineToolBar.running
                onClicked: pipelineToolBar.adapter.stepOnce()
            }
            ToolButton {
                text: qsTr("Run")
                visible: automationToggle.checked
                enabled: appController.hasVault && !pipelineToolBar.running
                onClicked: pipelineToolBar.adapter.startBatch(batchSizeSpin.value)
            }
            SpinBox {
                id: batchSizeSpin
                from: 1
                to: 100
                value: 1
                enabled: appController.hasVault && automationToggle.checked
            }
            ToolSeparator {}
            ToolButton {
                text: "⏸" // pause
                enabled: pipelineToolBar.running && !pipelineToolBar.paused
                onClicked: pipelineToolBar.adapter.pauseRun()
            }
            ToolButton {
                text: "▶" // resume
                enabled: pipelineToolBar.running && pipelineToolBar.paused
                onClicked: pipelineToolBar.adapter.resumeRun()
            }
            ToolButton {
                text: "⏹" // stop
                enabled: pipelineToolBar.running
                onClicked: pipelineToolBar.adapter.stopRun()
            }

            Item { Layout.fillWidth: true }

            Label {
                id: statusIndicator
                objectName: "statusIndicator"
                text: qsTr("Idle")
                font.bold: true

                Connections {
                    target: appController.pipelineAdapter
                    function onItemStarted(title) {
                        statusIndicator.text = qsTr("Processing: %1").arg(title)
                    }
                    function onItemCompleted(title) {
                        statusIndicator.text = qsTr("Completed: %1").arg(title)
                    }
                    function onItemErrored(title, error) {
                        statusIndicator.text = qsTr("Error (%1): %2").arg(title).arg(error)
                    }
                    function onRunFinished() {
                        statusIndicator.text = qsTr("Idle")
                    }
                }
            }
        }
    }

    NewVaultDialog { id: newVaultDialog; controller: appController }
    OpenVaultDialog { id: openVaultDialog; controller: appController }
    SettingsDialog { id: settingsDialog; controller: appController }

    SplitView {
        anchors.fill: parent
        orientation: Qt.Horizontal

        // Left dock area -- runs full height (Design spec: left/right zones
        // extend to the bottom edge, framing the bottom zone between them).
        TabbedDockArea {
            SplitView.preferredWidth: 260
            SplitView.minimumWidth: 160

            QueuePanel { queueModel: appController.queueModel }
            GitControlsPanel { gitController: appController.gitController }
        }

        // Center column: graph canvas above, bottom dock area below --
        // together they span the width between the left/right columns.
        ColumnLayout {
            SplitView.fillWidth: true
            spacing: 0

            GraphCanvasItem {
                id: graphCanvas
                objectName: "graphCanvas"
                Layout.fillWidth: true
                Layout.fillHeight: true
            }

            TabbedDockArea {
                objectName: "bottomDockArea"
                Layout.fillWidth: true
                Layout.preferredHeight: 220

                PipelineLogPanel { logModel: logModel }
            }
        }

        // Right dock area -- also runs full height.
        TabbedDockArea {
            SplitView.preferredWidth: 320
            SplitView.minimumWidth: 200

            AiChatPanel {}
            HealthDashboardPanel { healthController: appController.healthController }
        }
    }
}
